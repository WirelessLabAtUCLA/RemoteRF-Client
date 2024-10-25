from . import *
from ..common.utils import *

import getpass
import os
import datetime
import time
import ast

from prompt_toolkit import PromptSession

account = RemoteRFAccount()
session = PromptSession()

def welcome():
    printf("Welcome to Remote RF Account System.", (Sty.BOLD, Sty.BLUE))
    try:
        inpu = session.prompt(stylize("Please ", Sty.DEFAULT, "login", Sty.GREEN, " or ", Sty.DEFAULT, "register", Sty.RED, " to continue. (", Sty.DEFAULT, 'l', Sty.GREEN, "/", Sty.DEFAULT, 'r', Sty.RED, "):", Sty.DEFAULT))
        if inpu == 'r':
            print("Registering new account ...")
            account.username = input("Username: ")
            double_check = True
            while double_check:
                password = getpass.getpass("Password: ")
                password2 = getpass.getpass("Confirm Password: ")
                if password == password2:
                    double_check = False
                else:
                    print("Passwords do not match. Try again")
                    
            account.password = password
            account.email = input("Email: ")  # TODO: Email verification.
            # check if login was valid
            os.system('cls' if os.name == 'nt' else 'clear')
            
            if not account.create_user():
                welcome()
        else:
            account.username = input("Username: ")
            account.password = getpass.getpass("Password: ")
            # check if login was valid
            if not account.login_user():
                os.system('cls' if os.name == 'nt' else 'clear')
                print("Invalid login. Try again.")
                welcome()
    except KeyboardInterrupt:
        exit()
    except EOFError:
        exit()

def title():
    printf(f"Remote RF Account System", Sty.BOLD)
    # printf(f"Logged in as: ", Sty.DEFAULT, f'{account.username}', Sty.MAGENTA)
    printf(f"Input ", Sty.DEFAULT, "'help' ", Sty.BRIGHT_GREEN, "for avaliable commands.", Sty.DEFAULT)  

def commands():
    printf("Commands:", Sty.BOLD)
    printf("'clear' ", Sty.MAGENTA, "- Clear Terminal", Sty.DEFAULT)
    printf("'getdev' ", Sty.MAGENTA, "- View Devices", Sty.DEFAULT)
    printf("'help' ", Sty.MAGENTA, "- Show this help message", Sty.DEFAULT)
    printf("'perms' ", Sty.MAGENTA, "- View Permissions", Sty.DEFAULT)
    printf("'exit' ", Sty.MAGENTA, "- Exit", Sty.DEFAULT)
    printf("'getres' ", Sty.MAGENTA, "- View All Reservations", Sty.DEFAULT)
    printf("'myres' ", Sty.MAGENTA, "- View My Reservations", Sty.DEFAULT)
    printf("'cancelres' ", Sty.MAGENTA, "- Cancel a Reservation", Sty.DEFAULT)
    printf("'resdev' ", Sty.MAGENTA, "- Reserve a Device", Sty.DEFAULT)
    
def clear():
    os.system('cls' if os.name == 'nt' else 'clear')
    title()
    
def reservations():
    data = account.get_reservations()
    if 'ace' in data.results:
        print(f"Error: {unmap_arg(data.results['ace'])}")
        return
    entries = []

    for key, value in data.results.items():
        parts = unmap_arg(value).split(',')
        # Create a dictionary for each entry with named fields
        entry = {
            'username': parts[0],
            'device_id': int(parts[1]),  # Convert device_id to integer for proper numerical sorting
            'start_time': datetime.datetime.strptime(parts[2], '%Y-%m-%d %H:%M:%S'),  # Convert start_time to datetime
            'end_time': parts[3]
        }
        entries.append(entry)
        
    if (entries == []):
        printf("No reservations found.", Sty.BOLD)
        return
    
    printf("Reservations:", Sty.BOLD)

    # Sort the entries by device_id and then by start_time
    sorted_entries = sorted(entries, key=lambda x: (x['device_id'], x['start_time']))

    # Format the sorted entries into strings
    for entry in sorted_entries:
        printf(f'Device ID: ', Sty.RED, f'{entry["device_id"]}', Sty.MAGENTA, f', Start Time: ', Sty.RED, f'{entry["start_time"].strftime("%Y-%m-%d %H:%M:%S")}', Sty.BLUE, f', End Time: ', Sty.RED, f'{entry["end_time"]}', Sty.BLUE)
        
def my_reservations():
    data = account.get_reservations()
    if 'ace' in data.results:
        print(f"Error: {unmap_arg(data.results['ace'])}")
        return
    entries = []

    for key, value in data.results.items():
        parts = unmap_arg(value).split(',')
        # Create a dictionary for each entry with named fields
        entry = {
            'username': parts[0],
            'device_id': int(parts[1]),  # Convert device_id to integer for proper numerical sorting
            'start_time': datetime.datetime.strptime(parts[2], '%Y-%m-%d %H:%M:%S'),  # Convert start_time to datetime
            'end_time': parts[3]
        }
        entries.append(entry)
        
    if (entries == []):
        printf("No reservations found.", Sty.BOLD)
        return
    
    printf("Reservations under: ", Sty.BOLD, f'{account.username}', Sty.MAGENTA)

    # Sort the entries by device_id and then by start_time
    sorted_entries = sorted(entries, key=lambda x: (x['device_id'], x['start_time']))
    
    for entry in sorted_entries:
        if account.username == entry['username']:
            printf(f'Device ID: ', Sty.RED, f'{entry["device_id"]}', Sty.MAGENTA, f', Start Time: ', Sty.RED, f'{entry["start_time"].strftime("%Y-%m-%d %H:%M:%S")}', Sty.BLUE, f', End Time: ', Sty.RED, f'{entry["end_time"]}', Sty.BLUE)

def cancel_my_reservation():
    ## print all of ur reservations and their ids
    ## ask for id to cancel
    ## remove said reservation
    data = account.get_reservations()
    if 'ace' in data.results:
        print(f"Error: {unmap_arg(data.results['ace'])}")
        return
    
    entries:list = []

    for key, value in data.results.items():
        parts = unmap_arg(value).split(',')
        # Create a dictionary for each entry with named fields
        entry = {
            'id': -1,
            'internal_id': key,
            'username': parts[0],
            'device_id': int(parts[1]),  # Convert device_id to integer for proper numerical sorting
            'start_time': datetime.datetime.strptime(parts[2], '%Y-%m-%d %H:%M:%S'),  # Convert start_time to datetime
            'end_time': parts[3]
        }
        if account.username == entry['username']:
            entries.append(entry)
    
    printf("Current Reservation(s) under ", Sty.BOLD, f'{account.username}:', Sty.MAGENTA)
    
    sorted_entries = sorted(entries, key=lambda x: (x['device_id'], x['start_time'])) # sort by device_id and start_time
    for i, entry in enumerate(sorted_entries):  # label all reservations with unique id
        entry['id'] = i
        printf(f'Reservation ID: ', Sty.GRAY, f'{i}', Sty.MAGENTA, f' Device ID: ', Sty.GRAY, f'{entry["device_id"]}', Sty.BRIGHT_GREEN, f' Start Time: ', Sty.GRAY, f'{entry["start_time"].strftime("%Y-%m-%d %H:%M:%S")}', Sty.BLUE, f' End Time: ', Sty.GRAY, f'{entry["end_time"]}', Sty.BLUE)
        # print(f"Reservation ID {i}, Device ID: {entry['device_id']}, Start Time: {entry['start_time'].strftime('%Y-%m-%d %H:%M:%S')}, End Time: {entry['end_time']}")
        
    if sorted_entries == []:
        printf("No reservations found.", Sty.BOLD)
        return    
        
    inpu = session.prompt(stylize("Enter the ID of the reservation you would like to cancel ", Sty.BOLD, '(abort with any non number key input)', Sty.RED, ': ', Sty.BOLD))
    
    if inpu.isdigit():
        id = int(inpu)
        if id >= len(sorted_entries):
            print("Invalid ID.")
            return
        
        #grab the reservation
        for entry in sorted_entries:
            if entry['id'] == id:
                db_id = entry['internal_id']
                if session.prompt(stylize(f'Cancel reservation ID ', Sty.DEFAULT, f'{id}', Sty.MAGENTA, f' Device ID: ', Sty.DEFAULT, f'{entry["device_id"]}', Sty.BRIGHT_GREEN, f' Start Time: ', Sty.GRAY, f'{entry["start_time"].strftime("%Y-%m-%d %H:%M:%S")}', Sty.BLUE, f' End Time: ', Sty.DEFAULT, f'{entry["end_time"]}', Sty.BLUE, f' ? (y/n):', Sty.DEFAULT)) == 'y':
                    response = account.cancel_reservation(db_id)
                    if 'ace' in response.results:
                        print(f"Error: {unmap_arg(response.results['ace'])}")
                    elif 'UC' in response.results:
                        printf(f"Reservation ID ", Sty.DEFAULT, f'{id}', Sty.BRIGHT_BLUE, ' successfully canceled.', Sty.DEFAULT)
                else:
                    print("Aborting. User canceled action.")
                return
            
        print(f"Error: No reservation found with ID {id}.")
    else:
        print("Aborting. A non integer key was given.")

def devices():
    data = account.get_devices()
    if 'ace' in data.results:
        print(f"Error: {unmap_arg(data.results['ace'])}")
        return
    printf("Devices:", Sty.BOLD)
    
    for key in sorted(data.results, key=int):
        printf(f"Device ID:", Sty.DEFAULT, f' {key}', Sty.MAGENTA, f" Device Name: ", Sty.DEFAULT, f"{unmap_arg(data.results[key])}", Sty.GRAY)

def get_datetime(question:str):
    timestamp = session.prompt(stylize(f'{question}', Sty.DEFAULT, ' (YYYY-MM-DD HH:MM): ', Sty.GRAY))
    return datetime.datetime.strptime(timestamp + ':00', '%Y-%m-%d %H:%M:%S')

def reserve():
    try:
        id = session.prompt(stylize("Enter the device ID you would like to reserve: ", Sty.DEFAULT))
        token = account.reserve_device(int(id), get_datetime("Reserve Start Time"), get_datetime("Reserve End Time"))
        if token != '':
            printf(f"Reservation successful. Thy Token -> ", Sty.BOLD, f"{token}", Sty.BG_GREEN)
            printf(f"Please keep this token safe, as it is not saved on server side, and cannot be regenerated/reretrieved. ", Sty.DEFAULT)
    except Exception as e:
        printf(f"Error: {e}", Sty.BRIGHT_RED)

def perms():
    data = account.get_perms()
    if 'ace' in data.results:
        print(f"Error: {unmap_arg(data.results['ace'])}")
        return
    
    results = ast.literal_eval(unmap_arg(data.results['UC']))[0]
    printf(f'Permission Level: ', Sty.BOLD, f'{results[0]}', Sty.BLUE)
    if results[0] == 'Normal User':
        print(unmap_arg(data.results['details']))
    elif results[0] == 'Power User':
        printf(f'Max Reservations: ', Sty.DEFAULT, f'{results[3]}', Sty.MAGENTA)
        printf(f'Max Reservation Duration (min): ', Sty.DEFAULT, f'{int(results[4]/60)}', Sty.MAGENTA)
        printf(f'Device IDs allowed Access to: ', Sty.DEFAULT, f'{results[5]}', Sty.MAGENTA)
    elif results[0] == 'Admin':
        printf(f'No restrictions on reservation count or duration.', Sty.DEFAULT)
    else:
        printf(f"Error: Unknown permission level {results[0]}", Sty.BRIGHT_RED)

welcome()
clear()

while True:
    try:
        inpu = session.prompt(stylize(f'{account.username}@remote_rf: ', Sty.BLUE))
        if inpu == "clear":
            clear()
        elif inpu == "getdev":
            devices()
        elif inpu == "help" or inpu == "h":
            commands()
        elif inpu == "perms":
            perms()
        elif inpu == "quit" or inpu == "exit":
            break
        elif inpu == "getres":
            reservations()
        elif inpu == "myres":
            my_reservations()
        elif inpu == "resdev":
            reserve()
        elif inpu == 'cancelres':
            cancel_my_reservation()
        else:
            print(f"Unknown command: {inpu}")
    except KeyboardInterrupt:
        break
    except EOFError:
        break
        