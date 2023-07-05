# Google Drive Duplicate Finder

A Python script to identify duplicate files in Google Drive. It uses Google Drive API v3 for accessing and modifying files.

## Features

- Fetches metadata for all non-trashed files in the user's Google Drive.
- Compares files based on MD5 checksums.
- Detects duplicates and reports them in the console and a log file.
- Optionally moves duplicate files to the trash.

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

## Warning
Moving files to the trash is irreversible through this script. Be careful when using the `--delete` argument. If you accidentally move a file to the trash, you can manually restore it from the trash in Google Drive.
