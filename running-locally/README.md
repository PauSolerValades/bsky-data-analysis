# Running Locally

Problem: to edit in the server sucks, and as I care about code quality I end up with just trash code that I do not know how it does, and that is just unacceptable. Rather than spend time arguing with the system admin, I decided to have a small translator (llm made) between the usual code for starrocks with pymysql and duckdb python library.

`export_sample.py` samples a given % from the `records` and `posts` tables and exports them in parquet files.

Once this parquet files are in the local editing PC, every code should import in its UV project

```toml
   [tool.uv.sources]
   running-locally = { path = "../running-locally", editable = true }
 ```

where `running_locally.py` choses, according to the value of the environment variable `WHERE`, which sql library to use. In general, traeats the starrocks as default, and modifies the use of the duckdb library to behvabe as starrocks.
