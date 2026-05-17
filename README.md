# Firehose

## Data

There are two data sources:
1. /data/nfs/projects/bluesky-des/ contains folders with dates and hours and `.jsonl` files of all the events from 6 days of april.
2. `mysql -h 10.18.74.14 -P 9030 -u pau` has already some information from the `.jsonl` files dumped in there. See `docs/database-data-description.md` to undersand the data.

## Description and connection to the database

The database is a StarRock, very fast for statistics and analytics purpose. There are two databases rellevant:
- bsky: contains the tables described in `docs/database-data-description.md`. Just read access.
- pau_db: place were the results of reading the bsky database will be contained. Credentials in `.env`

## Project 1: topology-crawler

I need, from all the DID users in the data to know who follows who to be able to recreate the whole topology of the network.

## Project 2: Sessions descritpion

Need to define sessions lenghts and time between sessions. They are different per user and need some kind of strategy to aggrupate, as this is not infromations available in the data.

## Project 3: Lifetime of post

How much time is the post alive from it's creation? it's another metric to compare from the simulation.





