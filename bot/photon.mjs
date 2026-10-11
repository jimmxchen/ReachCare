// Bridges iMessage (Photon Spectrum) to the ReachCare server.
//   text message  -> text reply
//   voice memo    -> voice memo reply, in the language that was spoken
import { Spectrum, voice } from "spectrum-ts";
import { imessage } from "spectrum-ts/providers/imessage";

const REACH = process.env.REACH_URL || "http://127.0.0.1:8000";
const projectId = process.env.PHOTON_PROJECT_ID;
const projectSecret = process.env.PHOTON_PROJECT_SECRET;
if (!projectId || !projectSecret) {
  console.error("Set PHOTON_PROJECT_ID and PHOTON_PROJECT_SECRET in ../.env");
  process.exit(1);
}

// Phone numbers become E.164 (+15551234567) so web sign-ups and inbound texts match the same person.
function normalizeId(id) {
  if (!id) return undefined;
  const s = String(id).trim();
  if (s.includes("@")) return s.toLowerCase();
  const digits = s.replace(/\D/g, "");
  if (s.startsWith("+")) return `+${digits}`;
  if (digits.length === 10) return `+1${digits}`;
  if (digits.length === 11 && digits[0] === "1") return `+${digits}`;
  return s;
}

async function ask(body) {
  const res = await fetch(`${REACH}/api/bot/message`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `ReachCare returned ${res.status}`);
  return data;
}

const spaces = new Map(); // sender id -> space, so we can text people again while the bridge is running

// iMessage can redeliver an event, so answer each inbound message once. Prefer the provider's message id;
// otherwise treat the same sender repeating the same text within a few seconds as one message.
const seen = new Map(); // key -> time first handled
function alreadyHandled(message, sender, content) {
  const now = Date.now();
  for (const [k, t] of seen) if (now - t > 60_000) seen.delete(k);
  const fingerprint = content.type === "text" ? `${sender}|${content.text}` : null;
  const keys = [message.id ? `id:${message.id}` : null, fingerprint ? `fp:${fingerprint}` : null].filter(Boolean);
  const window = (k) => (k.startsWith("id:") ? 60_000 : 5_000);
  if (keys.some((k) => seen.has(k) && now - seen.get(k) < window(k))) return true;
  keys.forEach((k) => seen.set(k, now));
  return false;
}
const stamp = () => new Date().toISOString().slice(11, 23);
const app = await Spectrum({ projectId, projectSecret, providers: [imessage.config()] });
const im = imessage(app);
console.log("ReachCare bot is listening for iMessages…");

for await (const [space, message] of app.messages) {
  const content = message.content;
  const sender = normalizeId(message.sender?.id);
  if (sender) spaces.set(sender, space);
  if (alreadyHandled(message, sender, content)) {
    console.log(`${stamp()} [${sender}] duplicate delivery ignored (id ${message.id ?? "none"})`);
    continue;
  }
  console.log(`${stamp()} [${sender}] received ${content.type} (id ${message.id ?? "none"})`);
  try {
    let reply;
    if (content.type === "text") {
      reply = await ask({ sender, text: content.text });
    } else if (content.type === "voice" || (content.type === "attachment" && content.mimeType?.startsWith("audio/"))) {
      const bytes = await content.read();
      reply = await ask({ sender, audio_b64: Buffer.from(bytes).toString("base64"), mime: content.mimeType });
    } else {
      continue;
    }
    if (reply.audio_b64) {
      await space.send(voice(Buffer.from(reply.audio_b64, "base64"), {
        name: "reach-reply.m4a",
        mimeType: reply.audio_mime || "audio/mp4",
      }));
    } else {
      await space.send(reply.reply);
    }
    console.log(`${stamp()} [${message.sender?.id ?? "unknown"}] ${reply.mode} (${reply.language}) → replied`);
  } catch (err) {
    console.error("Could not answer:", err.message);
    await space.send("Sorry, I couldn't look that up right now. Please try again in a moment.");
  }
}

// Deliver queued texts (sign-up welcomes and clinic announcements), starting new chats when needed.
setInterval(async () => {
  try {
    const { messages } = await (await fetch(`${REACH}/api/bot/outbox`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })).json();
    const keep = [];
    for (const m of messages ?? []) {
      try {
        let space = spaces.get(m.sender);
        if (!space) {
          space = await im.space.create(await im.user(m.sender));
          spaces.set(m.sender, space);
        }
        await space.send(m.text);
        console.log(`texted ${m.sender}`);
      } catch (err) {
        console.error(`could not text ${m.sender}: ${err.message}`);
        if ((m.attempts ?? 0) < 4) keep.push({ ...m, attempts: (m.attempts ?? 0) + 1 });
      }
    }
    if (keep.length) {
      await fetch(`${REACH}/api/bot/requeue`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ messages: keep }) });
    }
  } catch { /* app not reachable; try again next tick */ }
}, 10000);
