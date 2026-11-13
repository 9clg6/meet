# Intent service page (`/intents`)

Meet can be the *service* of a [cozy intent](https://docs.cozy.io/en/cozy-stack/intents/):
cozy-stack frames `/intents?intent=<id>` in an iframe and the host application
talks to the page through `cozy-interapp`. The host gets
the call through the data and the results of the intent (join, leave, idle,
one error alone).

## How the page starts

1. It reads `intent` from the URL. Without it, it does nothing.
2. `POST /api/v1.0/stack-token/` (the Meet session) gives a stack access token
   and the `platform_url` of the user. The token is only sent to that
   platform, as a Bearer token, by the `fetch` given to `cozy-interapp`. It is
   never logged, stored or put in a URL.
3. `createService(intent)` opens the service. Without it in 10 s the page
   shows an error.
4. `getData()` must answer the data below, else the host gets
   `{ event: 'error', code: 'unsupported' }`. The page then joins the call and
   calls `notifyReadyToUse()` once.

## Data of the host

```json
{ "room": "<uuid>", "token": "<LiveKit token>", "audio": true, "video": false, "displayName": "optional" }
```

`room` is a lowercase UUID, `token` a non blank string, `audio` and `video`
booleans.

## Results to the host

Sent with `sendResult`, then `terminate()` after `left` or `error`:

| Result | When |
| --- | --- |
| `{ event: 'joined' }` | LiveKit is connected |
| `{ event: 'left', reason }` | `user`, `idle`, `removed` or `disconnected`, only after `joined` |
| `{ event: 'error', code }` | the join failed: `token_rejected`, `unreachable`, `unsupported` or `unknown` |

The host leaves with `{ leave: true }` (`service.onData`).

## No session

A 401 on the stack token shows a "Sign in to Meet" button (a popup needs a
click). It opens the Meet sign-in in a popup that comes back to
`/intents/signed-in`, which tells the page (`postMessage` to its own origin)
and closes itself. The page then asks for the stack token once more; a second
401 is the error `unauthenticated`. A blocked popup is `popup_blocked`. These
errors, and `unreachable` (409, 400, 5xx, network), are shown by the page
itself: no service exists yet to tell the host.

## Framing

`/intents` is framed by cozy-stack, so Meet must allow it in its
`Content-Security-Policy: frame-ancestors` (the origins of the Twake platforms
for `/intents`). `/intents/signed-in` is a popup and must **not** be framable.
This repository sets no framing header itself (the Django
`XFrameOptionsMiddleware` and the nginx of `src/frontend/default.conf` add
none): they come from the deployment (ingress), where `/intents` is allowed.
