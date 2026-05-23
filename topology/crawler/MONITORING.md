# Crawler Monitoring

## Is it still alive?

```bash
# Check if the tmux session exists
tmux ls | grep crawler

# See live output
tmux attach -t crawler
# (detach with Ctrl+B then D)
```

## Progress at a glance

```bash
# How many users crawled so far
mysql -h 10.18.74.14 -P 9030 -u pau -p'regulate-evil-decode' \
  -e "SELECT COUNT(*) AS crawled FROM pau_db.crawl_state;"

# How many API edges collected
mysql -h 10.18.74.14 -P 9030 -u pau -p'regulate-evil-decode' \
  -e "SELECT COUNT(*) AS api_edges FROM pau_db.crawled_followers;"

# Total edges (firehose + API)
mysql -h 10.18.74.14 -P 9030 -u pau -p'regulate-evil-decode' \
  -e "SELECT (SELECT COUNT(*) FROM pau_db.followers_from_data) + (SELECT COUNT(*) FROM pau_db.crawled_followers) AS total_edges;"
```

## Tail the log

```bash
# Last 30 lines
tail -30 topology-crawl/logs/crawler.log

# Follow live (Ctrl+C to stop watching)
tail -f topology-crawl/logs/crawler.log
```

## Kill / restart

```bash
# Graceful stop (Ctrl+C inside tmux)
tmux attach -t crawler
# then Ctrl+C — it will finish the current user and exit

# Kill forcefully
tmux kill-session -t crawler

# Restart from where it left off
cd ~/firehose-analysis
tmux new -s crawler -d \
  'uv run topology-crawl/crawl_followers.py 2>&1 | tee -a topology-crawl/logs/crawler.log'
```
