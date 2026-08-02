# Kit Control

A private parental-control remote for one family. It replaces the Qustodio
parent app for four actions — lock a child, unlock a child, lock everyone, read
current status — and nothing else. Everything else stays in the official app.

Cloudflare Worker + KV + a static PWA served from the same Worker. No native
app, no Xcode, no re-signing. Two phones load the same URL and paste the same
token.

## Before the first deploy

Three things are done by hand, in the Qustodio app. The first two are not
optional.

1. **Change the Qustodio account password.** The traffic capture this was built
   from contained it in plain text.

2. **Set Taysha's "Do Chores" routine to block devices completely.** It is
   `block_type: 0` while Selah's and Coen's equivalents are `block_type: 2`.
   Until it matches, locking Taysha is materially weaker than locking the other
   two, and locking her during a scheduled bedtime would *reduce* restrictions
   rather than increase them.

   This is a correctness prerequisite, so it is enforced in code rather than
   left to memory: `lockBlockedReason` is set for Taysha in `src/config.ts`, and
   until it is cleared, `POST /api/lock/taysha` returns `412 lock_disabled`,
   lock-all reports her as an error, and the PWA shows her Lock button disabled
   with the reason underneath. Status and unlock work for her throughout.

   **Once the routine is fixed, set `lockBlockedReason: null` for Taysha and
   redeploy.** That single edit ships lock for her.

3. **Resolve Taysha's duplicate routines.** "Chore time" and "Do Chores" hold
   overlapping schedules covering the same weekday hours, and which one wins is
   undefined. Disable or delete "Chore time". This app writes only to
   "Do Chores" (`f7d0dabc…`) for all three children; "Chore time" is recorded in
   `KNOWN_ROUTINE_UIDS` purely so it is unmistakably not the lock target.

## Deploy

```sh
npm install

# One KV namespace, then paste the returned id into wrangler.toml
npx wrangler kv namespace create KIT

npx wrangler secret put QUSTODIO_EMAIL
npx wrangler secret put QUSTODIO_PASSWORD
npx wrangler secret put QUSTODIO_CLIENT_ID       # from the Qustodio web SPA
npx wrangler secret put QUSTODIO_CLIENT_SECRET   # from the Qustodio web SPA
npx wrangler secret put KIT_CONTROL_TOKEN        # generate a long random string

npx wrangler deploy
```

Client id and secret belong to Qustodio's public web SPA and are extractable
from their JavaScript. They are not user secrets, but they live in env so they
can be rotated without a code change.

Generate the access token with something like:

```sh
openssl rand -base64 39 | tr -d '\n'
```

Then open the Worker URL on each phone, paste the token when prompted, and add
it to the home screen.

### First run

Two response shapes in the login flow were never captured, so the client logs
what it sees. After the first deploy, check `wrangler tail` for:

- `do_login response keys` — confirms which field carries the redirect URL. The
  client finds an `authorization_code=` anywhere in the body, so it should work
  regardless, but the log is what tells you it did.
- `access_token response keys` — confirms whether a `refresh_token` is issued.
  If it is, refresh is used automatically; if not, the client falls back to a
  full login. Either way, only key names are logged, never values.

## HTTP interface

Every route except `GET /` requires `Authorization: Bearer <KIT_CONTROL_TOKEN>`.

| Method | Path | Behaviour |
|---|---|---|
| GET | `/` | The PWA. No auth; the page prompts for the token and stores it in `localStorage`. |
| GET | `/manifest.webmanifest` | Web app manifest. |
| GET | `/api/status` | Cached status. `?fresh=1` bypasses the cache. |
| POST | `/api/lock/:child` | Slug is `taysha`, `selah`, or `coen`. |
| POST | `/api/unlock/:child` | |
| POST | `/api/lock-all` | `200` when every child locked, `207` when any did not. |
| GET | `/api/health` | Auth check plus one cheap GET against Qustodio. |

Errors are typed, because they need different responses:

```json
{"error":"auth_failed","detail":"Qustodio rejected credentials","ok":false,"spoken":"..."}
```

- `auth_failed` — login or refresh rejected. Password change or account lock.
- `api_changed` — unexpected status, or a response missing expected fields.
  Qustodio shipped a change.
- `verify_failed` — the write was accepted but state did not change.
- `lock_disabled` — a child's config prerequisite is outstanding (see above).
- `already_locked` / `already_unlocked` — not errors. `409` and `200`
  respectively, with a clear status flag.

The PWA renders each of these as its own message. None of them collapse into
"something went wrong".

## How a lock works

There is no lock endpoint in Qustodio's API. A lock is a *schedule* with
`overrides: true` written into one of the child's routines, which takes
precedence over their normal recurring schedules until its window expires, at
which point the normal schedule resumes with no further action.

The override goes into **Do Chores** for every child. That routine is a full
device block (`block_type: 2`) — the same block as Bedtime — so a lock is never
a downgrade even when it lands during a scheduled bedtime, and one fixed
duration (480 minutes) is correct regardless of what else is running. No
bedtime-aware duration logic is needed, and none exists.

**Status keys off the `overrides` flag, never `active_routine`.** The overrides
flag is the only reliable signal that a child is manually locked;
`active_routine` cannot tell a routine that happens to be running from one this
app forced on, and it is null often enough that keying off it would report a
locked child as free. `active_routine` is read for display only, and used to
label a scheduled bedtime distinctly.

`source` distinguishes a lock this app created (`kit`, matched against the
`override:{profileUid}` record in KV) from one set in the Qustodio app
(`qustodio`). KV is a fast path only — status works fine when it is missing,
which is exactly what happens for a lock set from the real app.

## Safety rails

The lock primitive writes into the same list that holds the family's real
recurring schedules. A wrong uid on a delete silently destroys a permanent rule,
with no undo and no audit trail in Qustodio. So:

- `deleteOverride` takes the **schedule object just read from the API**, not a
  uid, and refuses unless `overrides === true`. Tests cover `false`, missing,
  and a blank uid.
- `createOverride` hard-codes `overrides: true` and spreads it last, so no
  caller-supplied field can displace it. A response that comes back with
  `overrides: false` is rejected rather than treated as a lock.
- A schedule whose `overrides` field is missing or non-boolean is a parse
  error. Defaulting it either way is a foot-gun: `false` would hide a lock,
  `true` would arm the delete path against a permanent rule.
- Nothing here ever `PUT`s or `PATCH`es a routine, policy, or rules endpoint,
  and `invalidate_token` is never called — it would kill the session in the real
  app too.
- The profiles response carries GPS and street address per child. The client
  narrows it to four fields at the boundary, and `stripLocation` removes
  whereabouts keys at any depth from anything else parsed. Nothing downstream
  can log, store, or return a location.
- Status polling is capped at 15 minutes by the KV cache TTL. The PWA does one
  fetch on open plus manual refresh — no polling loop.

## Tests

```sh
npm test
npm run typecheck
```

91 tests, no network. `test/fake-qustodio.ts` stands in for the API and seeds
every child with permanent `overrides: false` schedules sitting in the same list
as the temporary ones, so the delete guard is exercised against the real hazard
rather than an empty list.

Covered: the time helper around midnight, month end, year end and both DST
boundaries; window containment (22:10 for 480 minutes contains 02:00 the
following day, across the spring-forward and fall-back nights); lock while
already locked returns `409` and issues no write; unlock with no override
returns `already_unlocked` and issues no delete; lock-all with one child failing
still reports the other two accurately; and the delete guard throwing on a
schedule with `overrides: false`.

### Live testing

On a real child profile, checking the Qustodio app between each step: lock,
status, unlock, status, lock while already locked, unlock twice. Do this with
Coen or Selah — not Taysha, until her prerequisite is done.

## Known gaps

- The `do_login` field carrying the redirect URL is unconfirmed. Handled
  defensively and logged on every login.
- Whether a `refresh_token` is issued is unconfirmed. Falls back to full
  re-login.
- Overlapping override behaviour is untested against the live API, because every
  capture deleted before creating. The `409` guard is what keeps it from
  mattering.
- Qustodio may flag unusual API traffic. Polling stays at 15 minutes or slower,
  and there are no retry loops.
- No official API exists, and Qustodio's terms prohibit unauthorised access to
  their internal one. This is a personal-use tool against the family's own
  account. It cannot be distributed.

## Not in v1

Delayed pause, add/remove time, routine scheduling, push notifications, undo
window, native widgets. The add-time endpoint notes are kept in the brief but
are deliberately not built.
