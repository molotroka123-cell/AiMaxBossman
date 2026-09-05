"""Search public licensed repository metadata without a GitHub token."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from solana_volume_suite.tools.github_hygiene import GitHubHygieneSearcher, GitHubSearchError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True)
    parser.add_argument("--min-stars", type=int, default=0)
    parser.add_argument("--language", default="Python")
    parser.add_argument("--output", default="github_hygiene_results.csv")
    args = parser.parse_args()
    searcher = GitHubHygieneSearcher()
    try:
        repos = searcher.filter_garbage(searcher.search_repositories(args.query, args.min_stars, args.language))
        searcher.save_to_csv(repos, args.output)
    except (ValueError, GitHubSearchError, OSError) as exc:
        parser.exit(1, f"Search failed: {exc}\n")
    for repo in repos:
        print(f'{repo["stargazers_count"]:>8}  {repo["full_name"]}  {repo["html_url"]}')
    print(f"{len(repos)} results saved to {args.output} (at most 100 candidates; heuristics only).")


if __name__ == "__main__":
    main()
