Sunbeam 1.7.0 beta 3: Ubisoft sign-in, take two.

- **Ubisoft "stay signed in" pass**: when the page you're on has no Remember me pass (after Ubisoft's "Welcome back!" page, say), the bookmark now asks Ubisoft for one on top of your sign-in.
- **No more Sunbeam tab**: the bookmark hands your sign-in over through a small Sunbeam window that closes itself, instead of turning your Ubisoft tab into Sunbeam. The sign-in no longer passes through a web address, so it can't end up in your browser's history.
- When there's still no pass, Sunbeam notes in its log where Ubisoft's page keeps its sign-in (names only, never the values), to find where Ubisoft moved it.

Sunbeam 1.7.0 beta 2: clearer Ubisoft sign-in.

- **Ubisoft's "Welcome back!" page**: when Ubisoft greets you with your account and a Continue button, there's no Remember me box, so Sunbeam couldn't get a "stay signed in" pass. The instructions now say to choose **Not you? Click here** there instead: it opens the full sign-in, where Remember me is.

Sunbeam 1.7.0 beta 1: Nintendo Switch syncs by itself, and every sign-in is encrypted.

- **Nintendo Switch and Switch 2 sync like any other store.** Sign in to your Nintendo Account once (Settings › Nintendo Switch), and every sync reads the Play Activity the Nintendo Store app shows: every game you've played, exact play time and days played, first and last played, Switch or Switch 2, and each day's play (added to the game's Play history). No more screen recordings.
- Signing in: Sunbeam opens Nintendo's own sign-in page in your browser; right-click **Select this account**, copy the link and paste it into Sunbeam. Your password only goes to Nintendo. The sign-in lasts about two years.
- Games you imported from screen recordings become the synced copies (matched by name), so nothing is counted twice and their statuses, art and shelves stay.
- **Every key, sign-in and account name is now encrypted** in `settings.json`, with Windows' own data protection, tied to your Windows account on this PC. Ones saved by earlier versions are encrypted the first time this version starts.
- The screen-recording import is still there (Sync › Nintendo Switch, without signing in).
