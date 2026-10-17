# GameShelf

All your games in one place, with art, details, playtime and time to beat.
Runs privately on your own PC: your keys and library never leave it.

## Get it

1. Download the latest **GameShelf zip** from [the releases page](https://github.com/Pookiiie/GameShelf/releases/latest) (under *Assets*). It's for Windows 10 and 11 and includes everything it needs: nothing else to install.
2. Right-click the zip › **Extract All**, into a folder you'll keep (for example `Documents\GameShelf`).
3. Open that folder and double-click **Start GameShelf**. If Windows says it protected your PC, choose **More info › Run anyway** (only the first time: GameShelf isn't signed with a paid certificate).
4. A short setup walks you through connecting your stores, and offers desktop and Start menu shortcuts.

GameShelf opens in its own window. While it runs, its icon by the clock reopens the window or quits it, and it stops by itself about a minute after you close the window (it waits for a sync to finish first).

**Updates:** when a new version is out, a bar at the top offers **Update now**. It replaces GameShelf's files and restarts; your `data` folder (library, keys, settings) is never touched. Settings › This PC also checks for updates and adds shortcuts.

**Moving from an older copy:** extract the new download, then copy your old `data` folder into it.

## Connect your accounts (in the app: Settings)

| Service | What it gives you | What you need |
|---|---|---|
| IGDB | Descriptions, genres, covers, time to beat | Free Twitch developer app (Client ID + Secret) |
| Steam | Owned games, playtime, last played, your wishlist | Web API key + your profile URL; profile "Game details" set to Public |
| PlayStation | PS4/PS5 games you've played, playtime, last played, your PlayStation Store wishlist | NPSSO token (lasts about 2 months) |
| Epic Games Store | Games you own (giveaways too), playtime | A one-time sign-in code from Epic's sign-in link in Settings; GameShelf stays signed in as long as you sync every few weeks |
| Xbox (and XGP) | Games you've played on Xbox or PC (Xbox app), playtime, last played | A free OpenXBL API key (sign in at xbl.io with your Microsoft account) |
| Ubisoft Connect | PC games you own, playtime and last played (from each game's Ubisoft stats, plus save dates from the Ubisoft Connect app on this PC) | A "Send to GameShelf" bookmark (drag it from Settings). Sign in at connect.ubisoft.com as usual, two-step code included, then click the bookmark there. Ubisoft only lets a real browser sign in, so click it again when you want fresh Ubisoft hours |
| Battle.net | Blizzard and Activision games you own; last played for games launched on this PC (Blizzard doesn't share playtime) | Open account.battle.net/api/games-and-subs while signed in and paste what it shows (Settings explains). Paste again after getting a new game |
| SteamGridDB (optional) | Nicer covers, banners and logos for console games, and lots of choices in Change art | Free API key |
| OpenCritic (optional) | Critic scores: top critic average, % recommended, tier | Free RapidAPI key (Basic plan: about 200 games a day) |
| HowLongToBeat | Main / Extras / 100% times | Nothing; set up automatically when possible |

Each section in Settings has step-by-step instructions and a **Save and test** button.

Switch 2 (and anything else) can be added with **Add game**: search, pick, and the art and details fill in.

For Nintendo Switch and Switch 2 games, use **Sync › Nintendo Switch › Import from a screen recording** (also under Add game). In the Nintendo Switch app on your phone, open your user page › Play Activity and tap your most recent game. Start a screen recording, swipe through the games one by one with a short pause on each, then drop the video into GameShelf. It reads each game's card: time played, first and last played, days played, the day-by-day list, and whether it's a Switch or Switch 2 game (from the badge). You check the list before anything is added. The day-by-day list shows up as **Play history** on the game's page. Importing again later updates dates and history, and raises hours but never lowers them. The first recording needs a one-time download of a small video tool (ffmpeg, about 30 MB). Screenshots of the Play Activity list still work too, with hours only.

## Using it

- **XGP and PS+ are part of your library.** Every sync also updates the XGP (Xbox PC Game Pass) list and the PS+ (PlayStation Plus Extra) Game Catalog (full games only: no Game Trials, demos or Premium Classics). Every game you can play through them sits in your library alongside the games you own, with the same Xbox or PlayStation icon: XGP counts as Xbox and PS+ as PlayStation, in the platform filters too. They get details, art, reviews and time to beat like any other game, and their page links to the store. The Library page counts the games you own and the extra ones on XGP and PS+ separately. "New on XGP/PS+" and "Leaving XGP" tags show on covers, and the "XGP and PS+" filters pick out recently added and leaving-soon games. "Recently added" counts from GameShelf's first look at the lists; Sony doesn't publish which PS+ games are leaving, so "Leaving soon" is XGP only. The first sync after this update looks up info for about 700 list games, which takes a while; your own games come first for OpenCritic, which stays within its daily caps.
- **Played / Never played** (filters): a game counts as played once a store reports any time or a last-played date for it. Never-played games show a dashed bar marked "Unplayed" with their time to beat on the right. Xbox knows when you last launched many older games but not for how long; those show "?h".
- **Three pages:** **Curator** (what opens first), **Library** and **Wishlist**, switched with the tabs next to the GameShelf logo. Typing in the search box on Curator jumps to the Library; on the Wishlist page it searches your wishlist. The game counts and filters are on the Library page.
- **Wishlist** shows your Steam and PlayStation wishlists together (a game on both appears once), grouped into **Out now** (newest first), **Coming soon** (soonest first, with the store's own date: a day, a month, a quarter or a year) and **No date yet**. Each cover has a heart with the Steam or PlayStation icon in its top-right corner, showing which wishlist it's on (your own copies keep the plain store icons at the bottom left). Under each cover: the release date for games not out yet (games already out show their review score instead), and the current price in Canadian dollars, with the usual price struck through when it's on sale. A "-40%" badge marks sales (hover for when the sale ends). Games you can already play say so next to the price ("On XGP", "On PS+" or "Owned on PS"). Games that are out show their Steam score (or OpenCritic's) with the source named, e.g. "94% on Steam". Pick a wishlist, show only games on sale, or sort by release date, date added, biggest discount, reviews or title (Coming soon always stays soonest first). Wishlist games get details, art, reviews and time to beat like any other, and open the same game page, which lists each wishlist with its release date, price and store page. GameShelf reads your wishlists at every sync but can't change them: add or remove games in the store. A game you buy leaves the Wishlist page and joins your library, keeping its art and details. Games that aren't out yet skip the time-to-beat and OpenCritic lookups until they're released.
- **Curator** has two columns. **Keep playing** (started, not finished) holds two lists: **Quick wins** (under 20 h to the credits; a game untouched for over a year counts its full length, as a restart) and **Worthwhile commitments** (20 h or more, but loved by players or critics: 85%+, ranked on reviews and your interest only, length doesn't count against them). **Try something new** (never played, including XGP and PS+ games) holds **Quick wins** (under 20 h to beat) and **Worthwhile commitments**. Five covers each, with the score and the hours ahead under each (hover for the full reason); a game appears in one list at most. Below them, **From your wishlist** has **Recently released** (out in the last 60 days, newest first) next to **Coming out soon** (a release date in the next 60 days, soonest first), and **On sale** (biggest discount first), each one row of small covers. Then **Recently played** shows your most recent games in one row, as many as fit the window. Quick wins are scored from reviews (70%: Steam's rating first, OpenCritic alongside; few Steam reviews lean on OpenCritic) and its time to the credits (30%: shorter is more inviting; a game you haven't touched in over a year counts in full, since you'd likely start over). The picks only change when your data does. Right-click any game, or use the buttons on its page, to say **More interested** (▲), **Less interested** (▼) or **Not right now** (left out of suggestions); picking the same again clears it. Completed, did-not-finish, hidden, VR and open-ended games aren't suggested. **Sort › Recommended** ranks the whole library the same way.
- **Automatic sync:** open GameShelf more than a day after the last sync and it syncs by itself (every library that's set up, the XGP and PS+ lists, and your wishlists).
- **Sync** pulls your games, playtime and last played from your stores. Games new to your library also get their details, art, reviews and time to beat. The first sync of a big Steam library can take 10–20 minutes (mostly time-to-beat lookups); later syncs take seconds unless you've bought new games.
- **Refresh assets** updates details, artwork, Steam reviews and time to beat for games you already have, either filling in only what's missing or re-fetching everything. Use it after setting up IGDB or SteamGridDB for the first time.
- Both windows list their steps in order, and show which are done and what's left while they run.
- Open a game to mark it **Completed** or **Did not finish**, add notes, fix a wrong match, or **Change art** (pick a cover, banner or logo from Steam, IGDB, PlayStation and SteamGridDB, or paste a link). Art you pick stays when you refresh assets.
- **Right-click** any game for a quick menu: open it, set its status, change art, fix the match or hide it.
- **Finished games are spotted for you.** Each sync checks the achievements and trophies of games you've played since the last check: a Platinum, every achievement, or one for finishing the story, rolling the credits or beating the final boss marks the game Completed. The game's page says which achievement did it. Open-ended games (multiplayer, sandbox, MMOs) are never marked, and an achievement only counts when it plainly means the whole game or story ("Complete the story", not "Complete a story case"). A status you set yourself is never changed. To check your whole library once, use Refresh assets with "Finished games" ticked.
- **Find duplicate games** (under the filters) lists games that appear more than once, such as Disco Elysium and its Final Cut, or a game and its tutorial. Pick which name to keep and combine them; playtime adds up and every copy is kept. The game's page then lists each version under Your copies with its own name, playtime and dates. "Not duplicates" stops a group from coming back.
- **Don't count** (on a game's page, per copy): hours to leave out of that copy's playtime, such as someone else playing on your account or earlier playthroughs when you only want your latest or longest one counted. They stay left out after later syncs; new play counts in full.
- **Time to beat slider:** on a game's page, slide between Main and 100% to match how you play that game. "Back to automatic" undoes it.
- The top of the Library page shows the games you own, the extra ones on XGP and PS+, and how many you've played this year.
- **Hide** anything that isn't really a game. Playtests, open betas, demos and test servers are hidden automatically; turn on **Show hidden games** in the filters to see them.
- OpenCritic's free plan allows 200 requests a day and RapidAPI can charge for going over, so GameShelf stays well under: at most 140 lookups a day (15 searches), no automatic retries, and it pauses for the day as soon as RapidAPI reports that less than a quarter of the allowance is left. A big library fills in over several days: each sync or Refresh assets picks up where the last one stopped.
- Filter by platform, status, played/never played and genres (tick several to see games in any of them). Untick **Include VR games** to leave out VR-only games. Sort by last played (games with hours but no date, such as old Steam or Epic games, come after dated ones; never-played games last), playtime, length, time to the credits, critic score, Steam reviews and more.
- The thin bar under each cover shows hours played on the left and hours to go on the right (a dashed bar means you haven't played it yet); it measures your playtime against Main + Extras. Once you've played 25% past that, it stretches towards Completionist (halfway there, at most 50% more). Green means completed, dotted means no time-to-beat data yet. Open-ended games (MMOs, sandbox, sports, or multiplayer games you've played far beyond their story) show ∞ instead of a bar; you can switch that on any game's page.
- Review scores come from OpenCritic (critics) and Steam (players), colour-coded from green (great) to red (poor) using OpenCritic's tiers (Mighty, Strong, Fair, Weak) and Steam's own ratings (Overwhelmingly Positive down to Negative).
- Covers that aren't the usual tall shape (PlayStation's square art) are shown whole over a blurred fill, so the grid stays even. Steam's official art is used first whenever a game is on Steam, unless Steam only has a wide image or its stand-in cover (the store banner over a smeared fill); then a tall cover from SteamGridDB is used, and if that's the wrong shape too, IGDB's or PlayStation's.

## Your data

Everything lives in the `data` folder next to the app:
- `library.db`: your library, statuses and notes
- `settings.json`: your keys and tokens
- `window`: the app window's own browser profile (its size and position)
- `gameshelf.log`: messages from the last runs, useful if something goes wrong

Back up that folder to keep your statuses and notes. Delete it to start fresh.

## Known limits

- PlayStation only reports games you've launched at least once. Add unplayed ones with Add game.
- The PlayStation wishlist comes from the PlayStation Store website's own query; if Sony changes it, the sync says it couldn't read the PlayStation wishlist (the rest of the sync carries on) until GameShelf is updated. A Steam wishlist needs your profile's game details to be public.
- Steam includes software and tools you own; they show up like games for now.
- A Ubisoft game you also own on Steam or Epic counts its hours once: the first time both show up, the copy with fewer hours is left out of the total (tick its Counts box on the game's page to change that).
- Battle.net games have no hours (Blizzard doesn't share them): ones launched on this PC show a last-played date and "?h"; the rest show as not played. A game GameShelf doesn't recognise yet is listed by its code when you choose Save and test.
