#!/usr/bin/env python3
import os
import sys
import logging
import argparse
import subprocess

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def run_cmd(cmd, check=True):
    logging.info(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        logging.error(f"Command failed: {result.stderr}")
        sys.exit(1)
    return result.stdout.strip()

def main():
    parser = argparse.ArgumentParser(description="Initialize, commit, and push project to GitHub")
    parser.add_argument("repo", help="GitHub repository URL (e.g., https://github.com/user/repo.git)")
    parser.add_argument("-b", "--branch", default="main", help="Target branch name")
    parser.add_argument("-m", "--message", default="UI overhaul and new routing", help="Commit message")
    parser.add_argument("-f", "--force", action="store_true", help="Force push to remote repository")
    args = parser.parse_args()

    if not os.path.exists(".git"): run_cmd(["git", "init"])

    run_cmd(["git", "add", "."])
    
    status = run_cmd(["git", "status", "--porcelain"], check=False)
    if not status:
        logging.info("No changes to commit. Proceeding to push.")
    else:
        run_cmd(["git", "commit", "-m", args.message])

    run_cmd(["git", "branch", "-M", args.branch])
    
    remotes = run_cmd(["git", "remote"], check=False)
    if "origin" in remotes.split():
        run_cmd(["git", "remote", "set-url", "origin", args.repo])
    else:
        run_cmd(["git", "remote", "add", "origin", args.repo])

    push_cmd = ["git", "push", "-u", "origin", args.branch]
    if args.force: push_cmd.append("--force")

    run_cmd(push_cmd)
    logging.info("System deployed to remote repository.")

if __name__ == "__main__":
    main()
