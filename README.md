# Telegram IPL Growth Kit

Ye project ek starter kit hai jo Telegram channel/group ko IPL live updates, fantasy tips, polls, aur viral CTA ke saath run karne me help karta hai.

## Kya included hai

- `bot.py`: Telegram bot scaffold with manual plus automatic live posting
- `autoposter.py`: one-shot auto poster for scheduled automation
- `register_task.ps1`: Windows Task Scheduler registration script
- `content_templates.py`: Daily post templates aur reusable captions
- `.env.example`: Required environment variables
- `requirements.txt`: Python dependencies

## Brand Positioning

Suggested names:

- IPL 2026 Live Score Fastest Update
- IPL Live + Fantasy Tips + Prediction
- IPL 2026 Live Score and Toss Update

Suggested bio:

```text
Ball-by-ball fastest update
Fantasy tips and captain picks
Match prediction and toss alert
Instant wicket and boundary update
```

## Daily Content System

### Morning (8 AM to 10 AM)

- Today match preview
- Playing 11 prediction
- Pitch report
- Fantasy captain and vice-captain picks

### Afternoon (2 PM to 4 PM)

- Poll post
- Match winner prediction
- Engagement question

### Match Time

- Toss update
- Score update every 2 to 3 min
- Wicket alert
- Boundary alert
- Innings break summary

### After Match

- Result
- Man of the match
- 3-line summary

## Growth System

### 1. Viral Forward CTA

Har post ke end me CTA add karo:

```text
Join Fastest IPL Live Score
@yourgroup
```

### 2. Giveaway Loop

- Small UPI or Paytm giveaway
- Ask users to invite 5 friends
- Collect screenshot in DM or form

### 3. Cross Promotion

- Telegram cross-promo
- Instagram reels with Telegram link
- WhatsApp status reposting

### 4. Channel + Group Model

- Channel = clean updates
- Group = discussion and engagement

## Setup

1. Python 3.11+ install karo.
2. `.env.example` ko copy karke `.env` banao.
3. `BOT_TOKEN`, `CHANNEL_CHAT_ID`, `GROUP_CHAT_ID`, `BRAND_HANDLE` set karo.
4. Automatic live updates ke liye `CRICKET_API_KEY` set karo.
   `CRICKET_API_BASE_URL` default `https://api.cricapi.com/v1/currentMatches` hai.
   Agar plan me 100 API calls per day milti hain to `AUTO_UPDATE_INTERVAL_SECONDS=900` rakho.
5. Dependencies install karo:

```bash
pip install -r requirements.txt
```

6. Bot run karo:

```bash
python bot.py
```

## No Manual Start Mode

Agar tum bina `python bot.py` chalaye automation chahte ho to ye use karo:

```powershell
pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File .\register_task.ps1
```

Is mode me:

- `autoposter.py` har 15 minute chalega
- IPL score/status changes post karega
- Group me daily match poll bhejega
- IPL news headlines bhi post karega

Ye state ko local file me save karta hai, isliye duplicate post repeat nahi honge.

## Commands

- `/start`
- `/help`
- `/preview MI CSK Wankhede batting-friendly Rohit Jadeja`
- `/poll MI CSK`
- `/toss MI elected to bat first`
- `/score RCB 85/2 10.3`
- `/wicket Virat OUT RCB 85/2 10.3`
- `/result MI won by 6 wickets Suryakumar`
- `/giveaway 1000`
- `/live_on`
- `/live_off`
- `/live_status`

## Important Notes

- Automatic live mode ke liye live cricket API key required hai.
- 100 calls/day limit ke liye default polling 15 minute par set hai, jo around 96 calls/day banta hai.
- True ball-by-ball live updates 100/day quota ke andar possible nahi hain; current setup snapshot-style automation hai.
- Ye starter kit content formatting aur quick-post flow ke liye optimized hai.
- Dream11 ya prediction related claims responsibly use karo. Overpromising avoid karo.
