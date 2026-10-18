A clean-up release: fixes from a full review of the app, no new features.

- **No more lost details when a service is down.** If Steam, IGDB or HowLongToBeat fails partway through a sync or Refresh assets, games keep the art, reviews and time to beat they already had instead of being cleared.
- **Combining duplicates keeps everything personal.** Your status (Completed, Did not finish), Curator marks, hidden and open-ended settings, your own time-to-beat choice and art you picked now carry over from whichever copy had them.
- **OpenCritic outages** are reported as a warning and skipped (still no retries, and the daily caps are unchanged). The "left for tomorrow" count is now right.
- **Safer settings file.** Settings are written in one step, so a crash can't leave a half-written file, and an unreadable settings file is set aside instead of being overwritten.
- **Library count:** with a search or filter on, it now reads like "4 of 1,540", counted against the games you'd normally see. Filter menus count only games the Type filter shows.
- Setup's last button says **Sync now** only when a store is ready to sync, otherwise **Done**.
- In Duplicate games, clicking the "Keep this name" text now picks it.
- Epic: a sign-in code Epic rejects no longer blocks syncs when Sunbeam is still signed in.
- The XGP / PS+ filter is ignored if you don't subscribe to either.
- Smaller fixes and leftovers removed (old Settings checks, unused code and styles). The README is up to date with the current Settings and filters.
