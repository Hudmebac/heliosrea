# Security and data policy

## Official surfaces

- **Site:** https://heliosrea.netlify.app/
- **Source:** https://github.com/Hudmebac/heliosrea

Only those two addresses are operated by this project. A fork, a mirror or a
copy on another domain is not this site and cannot change it.

## What is public, by design

- The source code and the generated static site.
- The Stripe **Payment Link** (a public checkout URL, not a secret key).
- The contact address printed on the public site.

A public payment link can be seen by anyone but only the owner's Stripe account
can receive or change its payouts. No Stripe secret key, webhook secret or API
key is stored in this repository.

## What is never published

These paths are ignored by git and stay local:

- `rea/data/investigations/` — investigation dossiers
- `rea/data/improvements.json` — change plans and execution records
- `rea/data/mailroom.json` — contacts, consent and issues
- `config.json` — local model configuration and keys
- `viewer/galaxy.json` — personal notes graph
- logs, local tooling and generated audio sources

The Mailroom API refuses every non-loopback request, for both reads and writes.
The Engine Room binds to `127.0.0.1` by default.

## Why a downloader cannot change this site

The published site is static HTML committed from the owner's repository. A
visitor has no write path: there is no public API, no form action and no
database on the Netlify host. The newsletter form opens the visitor's mail
client; it does not submit to this site. Changing the live site requires write
access to the owner's GitHub repository or Netlify project.

A person who downloads the source can change **their own copy**. That copy is
independent and cannot affect this site, its payment link or its contact list.

## Owner checklist

1. Use two-factor authentication on GitHub and Netlify.
2. Protect `main`: require a pull request and block force pushes.
3. Keep Netlify team access to the owner account only; review deploy logs.
4. Never commit `config.json`, `rea/data/`, `viewer/galaxy.json` or logs.
5. Rotate any key immediately if it is ever pasted into a tracked file.
6. Use a dedicated public contact address, not a personal inbox.
7. Re-check headers after a deploy: `Content-Security-Policy`,
   `X-Frame-Options: DENY`, `Strict-Transport-Security`.

## Reporting

Email the contact address on the public site with the URL, the observed
behaviour and the steps to reproduce it. Do not include secrets or personal
data. Reports about a copy on another domain should go to that host.

## Scope

This is a local-first tool with no hosted backend. There is no account system,
no remote login and no server-side storage on the public site. The most useful
reports concern the published site, the generated HTML, and the loopback-only
guards on the Mailroom and Improve APIs.
