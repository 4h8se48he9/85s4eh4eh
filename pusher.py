#!/usr/bin/env python3
import os
import sys
import logging
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
    print("="*50)
    print(" GitHub Auto-Pusher Interactive Terminal")
    print("="*50)
    
    # Get Repository
    repo = input("\nEnter GitHub repository URL (e.g., https://github.com/...):\n> ").strip()
    while not repo:
        print("Error: Repository URL cannot be empty.")
        repo = input("Enter GitHub repository URL:\n> ").strip()
        
    # Get Branch
    branch = input("\nTarget branch name [default: main]:\n> ").strip()
    if not branch:
        branch = "main"
        
    # Get Commit Message
    message = input("\nCommit message [default: UI overhaul and new routing]:\n> ").strip()
    if not message:
        message = "UI overhaul and new routing"
        
    # Force Push option
    force_input = input("\nForce push to remote? (y/N) [default: N]:\n> ").strip().lower()
    force = force_input == 'y'
    
    print("\n" + "="*50)
    print(" Starting Deployment...")
    print("="*50 + "\n")

    # Git logic
    if not os.path.exists(".git"): 
        run_cmd(["git", "init"])

    run_cmd(["git", "add", "."])
    
    status = run_cmd(["git", "status", "--porcelain"], check=False)
    if not status:
        logging.info("No changes to commit. Proceeding to push.")
    else:
        run_cmd(["git", "commit", "-m", message])

    run_cmd(["git", "branch", "-M", branch])
    
    remotes = run_cmd(["git", "remote"], check=False)
    if "origin" in remotes.split():
        run_cmd(["git", "remote", "set-url", "origin", repo])
    else:
        run_cmd(["git", "remote", "add", "origin", repo])

    push_cmd = ["git", "push", "-u", "origin", branch]
    if force: 
        push_cmd.append("--force")

    run_cmd(push_cmd)
    
    print("\n" + "="*50)
    logging.info("✅ System deployed to remote repository successfully.")
    print("="*50)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nOperation cancelled by user. Exiting.")
        sys.exit(0)