Sunbeam 1.7.1 beta 2: Steam reviews are back, and the Library's filters stay in view.

- **Fixed: Steam user reviews stopped updating on Oct 21.** Steam's old reviews page now redirects scripts to the store's front page, so new games (Tape to Tape, say) got no score and others weren't refreshed. Sunbeam now reads them from Steam's store service instead, 100 games per call, so the next sync fills them in quickly.
- **OpenCritic, again for new releases:** a game out in the last four months that OpenCritic didn't list yet is looked up again once a week (it was only wishlist games).
- **The Library's filters and sort stay in view** under the header while you scroll.

Sunbeam 1.7.1 beta 1: refunds leave, and no more silent freezes.

- **Games you no longer own are taken out.** When Steam, Epic, Ubisoft Connect or Battle.net stop listing a game (a refund, say), the next sync takes that copy out and says so in the sync log. Other copies of the game stay (Tape to Tape keeps its PlayStation copy). If a lot go missing at once, Sunbeam waits for the next sync to confirm, in case the store's list was incomplete; if half the list is missing, it does nothing.
- **Fixed: Sunbeam could stop answering after being open a long time.** Windows and the browser slow down a minimised window's page; Sunbeam took the quiet for a closed window and stopped in the background, leaving buttons that did nothing. It now checks whether its window is really there.
- **No more silent freezes.** If Sunbeam takes too long to answer, or has stopped, a bar at the top says so (with Reload or Try again), instead of buttons quietly doing nothing. What it was doing, and any error in the page, is written to its log so the cause can be found.
