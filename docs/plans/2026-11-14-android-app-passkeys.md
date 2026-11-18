# Android APP and learning-device login implementation plan

> **For Claude:** Execute this plan task by task; apply the Verification Before Completion skill before reporting completion.

**Goal:** Deliver an installable, signed Android APP, a working website download entry, revocable learning-device login and optional Passkey login; test and publish with `./build-deploy.sh 0`.

**Architecture:** Reuse the family H5 and its same-origin HttpOnly session in a small Android WebView. The existing uni-app project has no App ID or APP packaging configuration; use the previously evaluated online WebView option to avoid a vendor account/build service and a second set of image adapters. Store the native device credential encrypted with Android Keystore. Passkeys authorize child learning only; entering the parent centre always requires the guardian password. No face images or facial templates are collected.

**Tech Stack:** Existing Vue 3/uni-app H5, FastAPI/PostgreSQL; SimpleWebAuthn browser + py_webauthn; Android Java/WebView/AndroidX WebKit/Credential Manager.

## 1. Database and device login

- Add migration `0040_learning_device_login`: device credential hashes bound to revocable child sessions, Passkey public keys and expiring ceremonies, plus source-session links for renewed sessions.
- Add `learning_devices.py`: authenticated native enrollment, device resume and credential expiry. Revalidate consent/student/tenant state, retain revocation and use HttpOnly session cookies. No guardian credentials may be remembered by this API.
- Verify with isolated PostgreSQL tests: expiry, disabled/withdrawn students, wrong keys, revoked devices, logout, replay and child/guardian isolation. Existing 24-hour browser login behaviour must remain intact.

## 2. Passkeys

- Add `passkeys.py`: registration/authentication options and verification, child-only registration/list/revocation, one-time ceremony cookie, exact RP/origin and user-verification checks.
- Add client integration with `@simplewebauthn/browser`, capability detection, cancellation/error handling and credential management. Parent verification never offers Passkeys.
- Verify using real signed P-256 assertions and a browser virtual authenticator; reject wrong challenge/origin/signature, missing user verification, replay and revoked credentials.

## 3. Android APP and website download

- Add a minimal Android project and reproducible release build. Restrict WebView navigation and native messages to the configured origin; support camera/file upload, back, network errors, safe external links and foreground/session restoration.
- Store only child device keys using AES-GCM/Android Keystore; native code exchanges keys with HTTPS APIs and restores the HttpOnly cookie before opening the website.
- Add the login download entry, installation information, desktop QR code, release metadata and actual APK. Hide download within APP. Never show a fake iOS download.
- Keep release signing keys outside public artifacts/source and preserve them for upgrades. Publish APK, SHA-256 and Digital Asset Links with the family build.
- Verify release signature/package, downloaded artifact checksum, responsive UI, upload regressions and the device bridge. Real Android execution requires an emulator or attached device; report evidence precisely.

## 4. HTTPS, deploy and acceptance

- Production currently serves HTTP through a separate DNS gateway. HTTPS and domain access must be made available before secure device login/Passkeys can pass live acceptance. A question requesting the gateway SSH configuration or an APP subdomain is pending; continue independent implementation.
- Run backend tests, family unit/browser tests, family H5/WeChat builds, platform regressions, Android release build and real browser/API integration.
- Run `./build-deploy.sh 0`, check health, migrations, production page, download, package metadata and HTTPS authentication. Fix and redeploy failures. Mark complete only after all requested functionality is verified; do not substitute a disabled feature for completion.
