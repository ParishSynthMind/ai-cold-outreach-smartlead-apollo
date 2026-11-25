# Deliverability checklist: before the first real send

`python -m outreach health` checks the Smartlead accounts and your domain's DNS. `python -m outreach dns yourdomain.com` checks only DNS.

## 1. Domain
- [ ] Send from a **separate domain** (for example `getcarterautomation.com`), not the main company domain, so a bad week can't hurt the main domain's reputation.
- [ ] Domain at least 2 to 4 weeks old before sending; a simple website on it that redirects to the main site is fine.

## 2. DNS records (all three, checked by `dns`)
- [ ] **SPF**: exactly one TXT record starting `v=spf1`, including your mail provider (Google: `include:_spf.google.com`), ending `~all` or `-all`. Never `+all`. Keep it under 10 DNS lookups.
- [ ] **DKIM**: turn on signing in the mail provider and publish its key (Google Workspace selector is usually `google`; Microsoft 365 uses `selector1` and `selector2`). An empty `p=` means the key is revoked and the check fails.
- [ ] **DMARC**: a TXT record at `_dmarc.yourdomain.com`, starting with `v=DMARC1; p=none; rua=mailto:you@yourdomain.com`, then moving to `p=quarantine` once reports look clean.
- [ ] Custom tracking domain only if tracking is on (it is off by default here: plain text, no open or click tracking).

## 3. Mailboxes
- [ ] 2 to 3 mailboxes per sending domain, real names, profile photo, signature.
- [ ] Connected in Smartlead with SMTP and IMAP both passing (`health` fails otherwise).
- [ ] **Warm-up on for 2 to 4 weeks** before any campaign, and left on while sending (`health` warns if it is off).
- [ ] Start at 5 new leads a day per campaign (the default `daily_cap`), at most 20 to 30 emails a day per mailbox once warm.

## 4. List quality
- [ ] Only `verified` Apollo addresses (the tool drops everything else).
- [ ] Bounce rate under 2%; stop and clean the list if it goes above.
- [ ] Spam complaints under 0.1% (Gmail's hard line is 0.3%).

## 5. Content
- [ ] Plain text, no links in the first email, no attachments, no images.
- [ ] Short (the checks keep the first email to 35 to 130 words).
- [ ] One clear question as the call to action.
- [ ] Footer with name, company, postal address and unsubscribe line (added automatically).

## 6. After the first sends
- [ ] Send a test to your own Gmail and Outlook inboxes and check where it lands.
- [ ] Check Google Postmaster Tools for the sending domain once volume allows.
- [ ] Read every reply; keep `sync` or the webhook running so opt-outs are applied straight away.
