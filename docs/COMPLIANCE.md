# Compliance notes for cold email

This is not legal advice. These are the rules the tool is built around, written plainly, so a client
can see what it does and what is still their job. Laws change; check them for your own case.

## What the tool enforces in code

| Rule | How |
|---|---|
| Every lead has a reason it can be emailed | CSV rows need `consent_basis`; Apollo leads get the campaign's `lawful_basis` written onto every row. Config refuses to load without it. |
| Sender identified, postal address included | `sender_postal_address` is required; the footer (name, company, address, unsubscribe line) is added by code to every email, so the model can't drop or reword it. |
| A working way to opt out | Every email ends with the unsubscribe line; Smartlead's own unsubscribe text is set too. |
| Opt-outs honoured at once and everywhere | A reply containing stop wording is caught by keywords before any AI, then: local suppression list, unsubscribed in the Smartlead campaign, added to Smartlead's global block list. Bounces and Smartlead unsubscribes do the same. The local list is checked before every draft and every push, and works even if Smartlead is down. |
| Nothing goes out without a person | Drafts land in an approval queue. Flagged drafts need `--accept-warnings`; drafts with errors cannot be approved. `activate` needs `--yes`. |
| No invented claims | Links, numbers and "facts used" must appear in the lead row; misleading subject lines and spam wording are flagged. |
| Business addresses only | Apollo leads at personal mailbox domains (Gmail, Yahoo, Outlook and so on) and unverified addresses are dropped before drafting. |
| Low volume | `daily_cap` defaults to 5, is capped at 50, and is enforced both locally and in Smartlead's schedule. |
| No scraping | Only Apollo's and Smartlead's official APIs. Personal emails and phone numbers are never requested from Apollo. |

## United States: CAN-SPAM

- No false or misleading headers or subject lines.
- Say who you are and include a valid physical postal address.
- Give a clear way to opt out and honour it within 10 business days; don't charge for it or ask for more than an email address.
- You stay responsible even if someone else sends for you.
- CAN-SPAM does not require prior consent for business email, which is why cold B2B email is legal in the US when these rules are kept.

## United Kingdom: PECR and UK GDPR

- PECR's consent rule for marketing email covers "individual subscribers" (people, sole traders, some partnerships). Emails to employees at their corporate address (a limited company or LLP) don't need prior consent under PECR, but you must still identify yourself and give a valid opt-out address.
- UK GDPR still applies, because a named work email is personal data. The usual basis for B2B prospecting is **legitimate interest**, and that needs a written Legitimate Interests Assessment (purpose, necessity, balancing test), a relevant offer for the person's role, and a privacy notice.
- Article 14: when data was not collected from the person (it came from Apollo), tell them where you got it and how to object, at the latest in the first message or within a month. Put a short "where we got your details" line in your privacy notice and be ready to answer.
- Keep records: when and from where each address came, and the opt-out list, kept indefinitely so they are never emailed again.
- EU countries apply the ePrivacy rules differently (Germany, for example, is much stricter about B2B cold email). Use `allowed_countries` to stay within the places you have checked.

## United Arab Emirates

- The UAE Personal Data Protection Law (Federal Decree-Law 45 of 2021) expects a lawful basis, usually consent, for processing personal data, plus transparency and a way to object. Its executive regulations have been slow to arrive, so treat consent or an existing relationship as the safe default.
- The TDRA's rules on unsolicited electronic messages (the spam policy issued under the telecoms law) expect messages to identify the sender, carry a working unsubscribe, and only go to people who agreed or have a business relationship.
- Free zones have their own data laws (DIFC Data Protection Law 2020, ADGM Data Protection Regulations 2021), closer to GDPR, including legitimate interest with a balancing test.
- In practice: for UAE recipients keep volumes very low, use only clearly business-relevant contacts, and prefer warm or opted-in lists.

## Platform terms

- **Apollo:** data is licensed for your own prospecting under Apollo's terms; don't resell it or bulk-export it into another database. Respect the API rate limits (the free plan's search limit is 600 a day).
- **Smartlead:** its terms forbid spam, purchased lists and sending to people who opted out; accounts are suspended for high bounce or complaint rates.
- **Google and Microsoft mailboxes:** since 2024, senders to Gmail must authenticate (SPF, DKIM, DMARC), keep the spam complaint rate under 0.3% (aim for under 0.1%), and bulk senders must offer one-click unsubscribe. Workspace and Microsoft 365 terms also prohibit unsolicited bulk mail.

## What stays the client's job

Writing the Legitimate Interests Assessment, the privacy notice, choosing who to target, deciding whether a country is in scope, and getting legal advice for anything unusual. The tool makes the safe path the default; it can't make a bad list lawful.
