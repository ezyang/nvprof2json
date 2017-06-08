import sqlite3
import argparse
import shutil
import sys

def main():
    parser = argparse.ArgumentParser(description='Make modifications to nvprof output.')
    parser.add_argument('filename')
    parser.add_argument('--output', '-o', required=True, help="Output profile")
    args = parser.parse_args()

    shutil.copyfile(args.filename, args.output)
    conn = sqlite3.connect(args.output)
    conn.row_factory = sqlite3.Row

    print("Candidates:")
    last = float("Inf")
    for r in conn.execute("SELECT end FROM CUPTI_ACTIVITY_KIND_OVERHEAD ORDER BY end DESC LIMIT 10"):
        print(" * {}".format(r["end"]))
    # 3 is a magic number
    cutoff = conn.execute("SELECT end as max FROM CUPTI_ACTIVITY_KIND_OVERHEAD ORDER BY end DESC LIMIT 1 OFFSET 3").fetchone()["max"]
    print("Deleting all data before {}".format(cutoff))
    for table in ["CUPTI_ACTIVITY_KIND_RUNTIME", "CUPTI_ACTIVITY_KIND_DRIVER", "CUPTI_ACTIVITY_KIND_MEMCPY", "CUPTI_ACTIVITY_KIND_MEMSET", "CUPTI_ACTIVITY_KIND_CONCURRENT_KERNEL", "CUPTI_ACTIVITY_KIND_OVERHEAD"]:
        conn.execute("DELETE FROM {} WHERE start <= ?".format(table), (cutoff ,))

    conn.commit()
    conn.close()

def eprintRow(row):
    """Print a sqlite3.Row to stderr."""
    for k in row.keys():
        eprint("{}: {}".format(k, row[k]))
    eprint("----")

def eprint(*args, **kwargs):
    """Print to stderr."""
    print(*args, file=sys.stderr, **kwargs)

if __name__ == "__main__":
    main()
