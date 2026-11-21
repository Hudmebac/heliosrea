# Helios + Freebuff, without paying for an API

## The split

- **Helios is local-first and free.** It uses Ollama on this PC. The current model is `qwen3.5:4b`, which answers the full Helios prompt in roughly 4–6 seconds; `llama3.2:3b` is kept as a fallback option but is no longer the default.
- **Freebuff free is client-only.** It works inside the official Freebuff Desktop app or CLI. Freebuff's free tier deliberately refuses third-party API clients with `410 freebuff_update_required`.
- **A developer API key is not the free tier.** Codebuff's developer API uses paid credits; this account has none.

Helios therefore does not pretend to be a Freebuff API client. Instead it uses a manual handoff.

## Use Freebuff's free model with Helios

1. In Helios, click **⇄ Freebuff** in the “Talk to Helios” header.
2. Enter the question and click **Build briefing**.
3. Click **Copy briefing**.
4. Open **Freebuff Desktop**, or run the official CLI in a scratch folder:

   ```text
   mkdir C:\Users\craig\Freebuff-handoff
   cd /d C:\Users\craig\Freebuff-handoff
   freebuff
   ```

   Use a scratch folder so Freebuff cannot edit the Helios project.
5. Paste the briefing. It already contains the question, the relevant notes, mission state, and the rules. Ask Freebuff to answer only.
6. Copy Freebuff's answer, paste it into **Freebuff answer** in Helios, and click **Add answer to Helios**.

The answer is stored in the current Helios conversation and server-side history, so the local model can continue from it.

## Privacy and boundaries

- Helios does **not** send anything to Freebuff automatically. The briefing is copied only when you click **Copy briefing**.
- The briefing can contain note excerpts and mission state. Review it before pasting.
- Freebuff may still propose file edits or terminal commands. Do not apply them; the handoff is for an answer, not for giving Freebuff control of this project.
- No API key is needed for this workflow. Do not paste keys into the handoff.

## Why there is no automatic free bridge

Freebuff's free tier is gated on its own attested client. Automating or impersonating that client is an unsupported bypass and can put the account at risk. Helios does not do that.
