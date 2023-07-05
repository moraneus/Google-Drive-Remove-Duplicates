from __future__ import print_function
import os.path
import logging
import argparse
import pickle
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import sys

# Configure logging to write logs to a file and the console
log_format = '%(asctime)s - %(levelname)s - %(message)s'
logging.basicConfig(filename='drive_scanner.log', level=logging.INFO, format=log_format)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter(log_format))
logging.getLogger().addHandler(console_handler)

# If modifying these SCOPES, delete the file token.pickle.
SCOPES = ['https://www.googleapis.com/auth/drive']


def get_service():
    """Authorize and return Google Drive service."""
    creds = None
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)
    return build('drive', 'v3', credentials=creds)


def move_file_to_trash(service, file):
    """Move the given file to trash."""
    try:
        service.files().update(fileId=file['id'], body={'trashed': True}).execute()
        logging.info(f"Moved file {file['name']} with id {file['id']} to trash")
    except Exception as e:
        logging.error(f"An error occurred: {e}")


def fetch_all_files(service):
    """Fetch all file metadata from Google Drive and return as a list."""
    all_files = []
    page_token = None
    retrieved_files = 0
    while True:
        response = service.files().list(
            q="trashed = false",
            pageSize=1000, fields="nextPageToken, files(id, name, size, md5Checksum, trashed)",
            pageToken=page_token).execute()
        all_files.extend(response.get('files', []))
        logging.info(f"Retrieved {retrieved_files + 1000} file's metadata so far")
        retrieved_files += 1000
        page_token = response.get('nextPageToken', None)
        if page_token is None:
            break
    return all_files


def handle_duplicate(service, file1, file2, delete=False):
    """Log the duplicate and handle deletion if necessary."""
    logging.info(f"Duplicate found: {file1['name']} (ID: {file1['id']}) and {file2['name']} (ID: {file2['id']})")
    if delete:
        if file1['name'] != file2['name']:
            print(f"\nFound files with different names but same content:\n1. {file1['name']} "
                  f"(ID: {file1['id']})\n2. {file2['name']} (ID: {file2['id']})")

            choice = input("Enter 1 to move the first file to trash, "
                           "2 to move the second file to trash (or any other key to skip): ")
            if choice == '1':
                move_file_to_trash(service, file1)

            elif choice == '2':
                move_file_to_trash(service, file2)

            else:
                logging.info(f"No action was taken for duplicates files: "
                             f"{file1['name']} with id {file1['id']} and {file2['name']} with id {file2['id']}.")
        else:
            move_file_to_trash(service, file1)


def find_duplicates(service, delete=False):
    """Find duplicate files in Google Drive."""
    all_files = fetch_all_files(service)
    total_files = len(all_files)
    file_dict = {}
    for i, file in enumerate(all_files, 1):
        if 'md5Checksum' in file:
            duplicate = file_dict.get(file['md5Checksum'])
            if duplicate:
                handle_duplicate(service, file, duplicate, delete)
            else:
                file_dict[file['md5Checksum']] = file

        if i % 100 == 0 or i == total_files:
            logging.info(f"Checked {i} out of {total_files} files.")


def main():
    parser = argparse.ArgumentParser(description="Find duplicate files in Google Drive")
    parser.add_argument('--delete', action='store_true', help='Move duplicate files to trash')
    args = parser.parse_args()
    service = get_service()
    find_duplicates(service, delete=args.delete)


if __name__ == '__main__':
    main()
