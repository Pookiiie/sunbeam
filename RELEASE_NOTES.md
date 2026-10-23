Sunbeam 1.6.1 beta 4: OpenCritic keeps going by itself, and XGP / PS+ games are yours in previews.

- **OpenCritic scores fill in by themselves.** While Sunbeam is open, it reads the day's allowance (up to 180) in the background: a couple of minutes after it starts, then every half hour, picking up again when a new allowance starts at midnight UTC (8 pm in Toronto, 7 pm once the clocks go back). Settings › OpenCritic shows how many games are left and has a switch to turn it off.
- **Fixed: OpenCritic stopping for the day after a few lookups.** A quick burst of lookups got a "too many at once" from RapidAPI, and Sunbeam took it as "that's all for today". It now asks once a second, and a "slow down" only pauses 15 minutes. Today's pause is lifted.
- **Searches go further.** OpenCritic searches (15 a day) are no longer spent on games from before 2014, demos, playtests or betas, which OpenCritic doesn't have.
- **XGP and PS+ games are yours in previews**: "Yours on" with the Xbox or PlayStation icon, "with XGP". The game page no longer says "Not in your library" for them.

Sunbeam 1.6.1 beta 3: a cleaner Curator and richer previews.

- **No titles under the covers in Curator** (the cover shows the game; point at it for the preview). The picks keep their 1 to 5 numbers and their size.
- **Score and hours on their own lines** under the picks, In season and Hidden gems ("99% Steam", then "2.1h restart"; "96% Steam", then "740 reviews").
- **Previews tell you about your copy**: which stores you have it on (with their icons), or that it's on XGP or PS+; and your time with it, with a progress bar: "4.5 h played · about 33 h left", "Not played yet · about 4 h to beat", "played over a year ago · about 5 h from the start", or "open-ended".
- Previews also show the year next to the title, up to three genres, and the Steam and OpenCritic scores as small chips.
