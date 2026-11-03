Sunbeam 1.8.0 beta 4: remakes aren't taken for the original any more.

- **Fixed: a remake or port named like an older game was identified as the older game.** The Legend of Zelda: Ocarina of Time on your Nintendo wish list showed the 1998 game; Paper Mario: The Thousand-Year Door and Star Fox did too. Sunbeam now looks for the game on the store's platform and, for wishlist games, around the store's release date. Your next sync identifies again the games this got wrong, Switch games matched to their Wii U release included (when IGDB has the Switch release). Games you matched by hand stay as they are.
- Store names with curly apostrophes (Nintendo's "Fortune’s Weave") are found on IGDB.
- **The Switch icon fits in its wishlist heart** (it's a little smaller than the round Steam one).

Sunbeam 1.8.0 beta 3: a clean start for the Nintendo sign-in.

- **Each Sign in to nintendo.com starts from an empty Edge profile,** so nothing is left from an earlier try. Nintendo's sign-in can keep refusing a browser it has refused once (the error with a Reference Code), and the window kept the first try's cookies.
- **If Nintendo's site turns away the window Sunbeam reads your wish list in** (its protection against automated browsers), Settings now says so plainly and Sunbeam stops trying by itself; prices for the games on the list keep updating. Try again is in Settings › Nintendo Switch › Wish list.
- What that window got from Nintendo's site (which page, and its answer) is noted in Sunbeam's log, so a problem can be traced.

Sunbeam 1.8.0 beta 2: signing in to nintendo.com works.

- **Fixed: Nintendo's sign-in showed an error with a reference code.** Nintendo turns away sign-ins in a browser window that a program is driving, and Sunbeam's sign-in window was one. It's now an ordinary Edge window that Sunbeam doesn't touch: sign in, wait for your wish list to show, and close the window. Sunbeam reads your wish list then, and at every sync.

Sunbeam 1.8.0 beta 1: your Nintendo wish list, with eShop prices and sales.

- **Nintendo wish list:** Settings › Nintendo Switch › Wish list › **Sign in to nintendo.com**. An ordinary Edge window opens with a profile of Sunbeam's own (not your everyday browser); sign in there once and, when your wish list shows, close the window: Sunbeam reads it then. From then on every sync reads it in a window out of sight. Your password only goes to Nintendo, and Edge keeps the sign-in encrypted for your Windows account.
- **eShop prices and sales** for those games, in Canadian dollars, next to Steam, PlayStation, GOG and Epic on the Wishlist page and each game's page, with "-20%" badges and when the sale ends. If Nintendo's sign-in in that window ends, Settings says so, the list stays as it was, and prices keep updating from Nintendo's public price service.
- Games you own on Switch or Switch 2 show "Owned" in the eShop box instead of a price.

Sunbeam 1.7.1 beta 3: Keen or not? shows more of each game.

- **Screenshots**: a big one with up to six more under it (point at one to see it large, click to open them full size).
- **What players say on Steam**: the three most helpful English reviews, with a critical one among them when there's one near the top, how long each reviewer had played and how many found it helpful; a link to more on Steam. The next game's reviews are fetched while you decide on this one.
- Steam and OpenCritic scores as chips, with the number of reviews; the summary has more room. The window is wider.

Sunbeam 1.7.1 beta 2: Steam reviews are back, and the Library's filters stay in view.

- **Fixed: Steam user reviews stopped updating on Oct 21.** Steam's old reviews page now redirects scripts to the store's front page, so new games (Tape to Tape, say) got no score and others weren't refreshed. Sunbeam now reads them from Steam's store service instead, 100 games per call, so the next sync fills them in quickly.
- **OpenCritic, again for new releases:** a game out in the last four months that OpenCritic didn't list yet is looked up again once a week (it was only wishlist games).
- **The Library's filters and sort stay in view** under the header while you scroll.

Sunbeam 1.7.1 beta 1: refunds leave, and no more silent freezes.

- **Games you no longer own are taken out.** When Steam, Epic, Ubisoft Connect or Battle.net stop listing a game (a refund, say), the next sync takes that copy out and says so in the sync log. Other copies of the game stay (Tape to Tape keeps its PlayStation copy). If a lot go missing at once, Sunbeam waits for the next sync to confirm, in case the store's list was incomplete; if half the list is missing, it does nothing.
- **Fixed: Sunbeam could stop answering after being open a long time.** Windows and the browser slow down a minimised window's page; Sunbeam took the quiet for a closed window and stopped in the background, leaving buttons that did nothing. It now checks whether its window is really there.
- **No more silent freezes.** If Sunbeam takes too long to answer, or has stopped, a bar at the top says so (with Reload or Try again), instead of buttons quietly doing nothing. What it was doing, and any error in the page, is written to its log so the cause can be found.
