# Shortcuts setup

Five shortcuts, each one a single **Get Contents of URL** action followed by
**Show Result**. Build them once on one phone, then repeat on the other — or
share each one via AirDrop and re-enter the token.

Once created, they appear in Control Centre, on the Lock Screen, and on the
Action Button with no extra work.

You will need:

- your Worker URL, e.g. `https://kit-control.<your-subdomain>.workers.dev`
- the `KIT_CONTROL_TOKEN` value

## Build one shortcut

1. Shortcuts app → **+**
2. Add action → search **Get Contents of URL**
3. **URL**: `https://<worker>/api/lock/coen`
4. Tap the arrow to expand the action:
   - **Method**: `POST` (use `GET` for the status shortcut)
   - **Headers**: add one — key `Authorization`, value `Bearer <token>`
5. Add action → search **Show Result** → set its input to the
   **Contents of URL** output
6. Rename the shortcut (tap the name at the top) to one of the names below

The Worker returns a `spoken` field with a short natural sentence, so the
shortcut needs no logic of its own — Siri reads the outcome straight out.
Failures say so plainly: *"Couldn't lock Coen. Check the app."*

## The five shortcuts

| Name | Method | URL |
|---|---|---|
| Lock Coen | POST | `https://<worker>/api/lock/coen` |
| Unlock Coen | POST | `https://<worker>/api/unlock/coen` |
| Lock Selah | POST | `https://<worker>/api/lock/selah` |
| Unlock Selah | POST | `https://<worker>/api/unlock/selah` |
| Lock everyone | POST | `https://<worker>/api/lock-all` |

Optionally also:

| Name | Method | URL |
|---|---|---|
| Kid status | GET | `https://<worker>/api/status` |

### Taysha

Do not build "Lock Taysha" yet. Until her Do Chores routine is set to block
devices completely (see the README), `POST /api/lock/taysha` returns `412` and
Siri will say *"That child can't be locked yet. Check the app."*

"Unlock Taysha" is safe to build now — unlock is never gated.

Once the routine is fixed and `lockBlockedReason` is cleared in `src/config.ts`,
add "Lock Taysha" pointing at `https://<worker>/api/lock/taysha`. No shortcut
changes are needed anywhere else.

## Naming

Name them for speech, and avoid names that collide with Qustodio's own Siri
vocabulary. "Lock Coen" and "Lock everyone" are safe; anything starting with
"Qustodio" is not.

## What each one says

- Lock: *"Coen is locked until 6:10 AM."*
- Lock when already locked: *"Coen is already locked until 6:10 AM."*
- Unlock: *"Coen is unlocked."*
- Unlock during a scheduled bedtime: *"Coen is unlocked, but bedtime is still
  scheduled."*
- Unlock with nothing to remove: *"Coen was already unlocked."*
- Lock everyone, all good: *"Everyone is locked: Taysha, Selah, Coen."*
- Lock everyone, partial: *"Locked Selah and Coen. Couldn't lock Taysha. Check
  the app."*

The partial case is deliberate. A blanket "done" that hides one child's failure
is the exact bug this app exists to avoid, so lock-all always names who did not
get locked.

## Troubleshooting

- *"Kit Control rejected the token."* — the `Authorization` header is wrong.
  It must read `Bearer <token>`, with the space.
- Siri reads out raw JSON — the **Show Result** input is set to the whole
  response instead of just the text. Either is fine; setting the URL action's
  response to **JSON** and picking the `spoken` key reads better.
- Nothing happens at all — check `https://<worker>/api/health` in a browser
  first; it needs the same bearer header, so easiest is to test from the PWA.
