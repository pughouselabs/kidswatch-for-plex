# Live onboarding and playback checks

Use test accounts and a database backup before checking a real installation.
Local fake-Plex tests verify app behavior; they do not prove a real client stops.

- [ ] Sign in as the actual Plex server owner and receive the private sign-in code.
- [ ] Open Admin > Owner setup. Choose only intended movie and TV libraries.
- [ ] Send a notification test, check the receiving device, then confirm receipt.
- [ ] Confirm the desired trusted-parent setting and finish setup. Existing installs
      must retain per-child approval until the owner explicitly enables trust.
- [ ] Invite an adult who accepted Plex access. Sign in as that account, claim the
      invitation, and confirm a parent request reaches the owner's private device.
- [ ] Review the parent request, approve it, and check that the existing waiting
      page opens My kids without another sign-in. Check decline and revoke too.
- [ ] In the regular-account connection step, check the child's name in the email
      suggestion. Confirm the correct Plex account in a private window.
- [ ] With trust on, verify restrictions and titles, immediate child activation,
      one owner notification, and no separate child approval request.
- [ ] With trust off, verify a newly connected child has no visible titles until
      the owner approves. Earlier pending children must stay pending after a toggle.
- [ ] On a real player, start an allowed video. Confirm pause, daily time expiration,
      and watch-hour expiration each stop playback. Confirm blocked titles and
      libraries are inaccessible, downloads are disabled, and online-source status
      accurately reports any manual follow-up.
- [ ] Repeat essential pages at 390px and desktop with keyboard navigation.

## Undo

Before an upgrade, stop the app and take a SQLite backup using `.backup`, including
any required deployment configuration backup. Revert the integration commit or
restore the prior application image. Onboarding tables and settings are additive
and may remain with older code. To restore the exact pre-upgrade app state, restore
the matching database backup while the app is stopped.

Reverting code or a trust setting does not retract Plex shares already granted.
Pause or remove affected children in Admin, verify the real Plex share is removed,
and use the parent's site-access control if their access must also be revoked.
