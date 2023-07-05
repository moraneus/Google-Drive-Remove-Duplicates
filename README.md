# Google Drive Duplicate Finder

This script helps you find duplicate files in your Google Drive using the Google Drive API. 
Duplicates are determined based on the MD5 hash of files, which ensures high accuracy in duplicate detection. 
By comparing file hashes, we can be confident that the file content is identical, making this approach very reliable and safe.
It uses Google Drive API v3 for accessing and modifying files.

## Features

- Fetches all non-trashed files metadata from your Google Drive.
- Identifies duplicate files by comparing their hash values (MD5 checksums).
- Optionally moves duplicate files to trash.
- If it finds files with the same content but different names, it will ask you which file you want to keep.
- Logs each step of the process to help you track the script's progress and actions.

## Installation

1. Clone the repository:

```bash
git clone https://github.com/moraneus/G-Drive-Remove-Duplicates.git
cd G-Drive-Remove-Duplicates
```
2. Install the required Python packages:
```bash
pip install -r requirements.txt
```
3. Setup Google Cloud Project:
	1. Create Google Cloud project.
	2. Enable the Google Drive API.
	3. Download the client configuration as described [here](https://developers.google.com/drive/api/v3/quickstart/python).
	4. Save the configuration file as `credentials.json` in the same directory as the script.
	5. Add your Google Account as a test user.

## Usage
Run the script without any arguments to only report duplicates:
```bash
python duplicate_scanner.py
```

Run the script with the `--delete` argument to move duplicates to the trash:
```bash
python duplicate_scanner.py --delete
```

### Delete Behavior
When the --delete argument is used, the behavior is as follows:

- Duplicates with the same file name: One of the duplicate files is automatically moved to the trash without user intervention.

- Duplicates with different file names: The script will prompt the user to choose which file to move to the trash.

In both cases, the operation is reported in the console and the log file.

## Logs
The script writes detailed logs to drive_scanner.log. Each run of the script appends to the log file.

## Cleanup Instructions

After you have finished using the application, it is recommended to perform the following cleanup steps:

1. Delete OAuth 2.0 Client ID: Go to [Google Project Credentials](https://console.cloud.google.com/apis/credentials) page. Locate the OAuth 2.0 Client ID credential and delete it.
2. Revoke Access: Go to your [Google Account Permissions](https://myaccount.google.com/permissions) page. Locate the application name you used for this project and click on it. Then, click on the "Remove Access" or "Revoke Access" button to revoke the permissions granted to the application.
3. Remove Test User (if applicable): If you added your Google Account as a test user to bypass the verification process.
4. Delete your project.
5. Delete the generated `token.json` file created locally on your project directory.
6. Delete the `credentials.json` file from your project directory.

By following these cleanup instructions, you can ensure that the application no longer has access to your Google Account and that any test user permissions are removed.


## Warning
Moving files to the trash is irreversible through this script. Be careful when using the `--delete` argument. If you accidentally move a file to the trash, you can manually restore it from the trash in Google Drive.
