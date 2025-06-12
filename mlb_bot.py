import requests
import openai
import os
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
import csv

# ---------------------- CONFIG ----------------------
OPENAI_API_KEY = "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"
ODDS_API_KEY = "30286ce4095c7cf791cefb78399a6793"
EMAIL_USER = "youremail@gmail.com"
EMAIL_PASS = "yourapppassword"
EMAIL_RECEIVER = "receiveremail@example.com"
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

openai.api_key = OPENAI_API_KEY
logging.basicConfig(level=logging.INFO, filename='mlb_bot_debug.log', filemode='a',
                    format='%(asctime)s - %(levelname)s - %(message)s')

# ---------------------- FETCH ODDS ----------------------
def fetch_odds():
    url = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": "h2h,spreads,totals",
        "oddsFormat": "american"
    }
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logging.error(f"Odds fetch error: {e}")
        return []

# ---------------------- FETCH TEAM STATS ----------------------
def fetch_team_stats(team_name):
    try:
        teams_url = "https://statsapi.mlb.com/api/v1/teams?sportId=1"
        teams_resp = requests.get(teams_url).json()
        team_id = next((t["id"] for t in teams_resp["teams"] if team_name.lower() in t["name"].lower()), None)
        if not team_id:
            logging.warning(f"Team ID not found for {team_name}")
            return {}

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        schedule_url = f"https://statsapi.mlb.com/api/v1/schedule?teamId={team_id}&sportId=1&startDate=2024-06-01&endDate={today}"
        schedule = requests.get(schedule_url).json()
        games = [g["games"][0] for g in schedule.get("dates", []) if g["games"]]

        bullpen_innings = 0
        game_dates = []

        for game in reversed(games[-3:]):
            game_dates.append(game["officialDate"])
            game_pk = game["gamePk"]
            box_url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
            boxscore = requests.get(box_url).json()

            side = "home" if game["teams"]["home"]["team"]["id"] == team_id else "away"
            pitchers = boxscore["teams"][side].get("pitchers", [])
            players = boxscore.get("players", {})

            for pid in pitchers:
                key = f"ID{pid}"
                stats = players.get(key, {}).get("stats", {}).get("pitching", {})
                if not stats.get("gamesStarted", 0):
                    innings = stats.get("inningsPitched", 0)
                    if isinstance(innings, str):
                        if "." in innings:
                            whole, frac = innings.split(".")
                            innings = int(whole) + int(frac) / 3
                        else:
                            innings = float(innings)
                    bullpen_innings += innings

        last_game_date = datetime.strptime(game_dates[-1], "%Y-%m-%d") if game_dates else datetime.now(timezone.utc)
        rest_days = (datetime.now(timezone.utc) - last_game_date.replace(tzinfo=timezone.utc)).days

        win_streak = 0
        for game in reversed(games):
            is_home = game["teams"]["home"]["team"]["id"] == team_id
            result = game["teams"]["home"]["isWinner"] if is_home else game["teams"]["away"]["isWinner"]
            if result:
                win_streak += 1
            else:
                break

        series_games = [g for g in games if g.get("seriesGameNumber") and g.get("gameType") == "R"]
        series_losses = sum(
            1 for g in series_games
            if (g["teams"]["home"]["team"]["id"] == team_id and not g["teams"]["home"]["isWinner"])
            or (g["teams"]["away"]["team"]["id"] == team_id and not g["teams"]["away"]["isWinner"])
        )

        return {
            "bullpen_innings_last_3": bullpen_innings,
            "rest_days": rest_days,
            "win_streak": win_streak,
            "series_record": {"losses": series_losses}
        }
    except Exception as e:
        logging.error(f"Failed to fetch team stats: {e}")
        return {}

# ---------------------- GPT ANALYSIS ----------------------
def analyze_game_with_gpt(game):
    try:
        home = game.get("home_team", "Unknown")
        away = game.get("away_team", "Unknown")
        matchup = f"{away} vs {home}"

        home_stats = fetch_team_stats(home)
        away_stats = fetch_team_stats(away)

        filters_triggered = []
        if home_stats.get("bullpen_innings_last_3", 0) > 10:
            filters_triggered.append("High Bullpen Fatigue")
        if home_stats.get("rest_days", 3) == 0:
            filters_triggered.append("Travel Fatigue")
        if away_stats.get("win_streak", 0) >= 4:
            filters_triggered.append("Hot Streak")

        prompt = f"Analyze the MLB matchup between {away} and {home}. Consider team stats, bullpen fatigue, win streaks, and travel rest factors. Filters triggered: {filters_triggered}"

        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a professional MLB betting analyst. Provide clear betting insights based on data."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=500
        )

        analysis_text = response.choices[0].message["content"].strip()

        return {
            "game": matchup,
            "analysis": analysis_text
        }
    except Exception as e:
        logging.error(f"GPT analysis failed: {e}")
        return {"game": "Unknown", "analysis": f"Error: {e}"}

# ---------------------- CSV SAVE ----------------------
def save_results_to_csv(results):
    try:
        filename = f"mlb_betting_results_{datetime.now().strftime('%Y-%m-%d')}.csv"
        with open(filename, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["game", "analysis"])
            writer.writeheader()
            for row in results:
                writer.writerow(row)
        return filename
    except Exception as e:
        logging.error(f"Failed to save CSV: {e}")
        return None

# ---------------------- EMAIL REPORT ----------------------
def send_email_report(file_path):
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_USER
        msg['To'] = EMAIL_RECEIVER
        msg['Subject'] = "MLB Betting Report"

        with open(file_path, 'r') as f:
            msg.attach(MIMEText(f.read()))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASS)
            server.sendmail(EMAIL_USER, EMAIL_RECEIVER, msg.as_string())
        print("Email sent.")
    except Exception as e:
        logging.error(f"Email failed: {e}")
        print("Failed to send email.")

# ---------------------- MAIN ----------------------
if __name__ == "__main__":
    odds = fetch_odds()
    if not odds:
        print("No games found.")
    else:
        results = []
        for game in odds:
            result = analyze_game_with_gpt(game)
            print(result)
            results.append(result)
        path = save_results_to_csv(results)
        if path:
            send_email_report(path)

