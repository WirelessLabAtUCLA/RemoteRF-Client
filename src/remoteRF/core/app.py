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
    printf("Welcome to RemoteRF Reservation System. (all times are in Pacific Time)", (Sty.BOLD, Sty.BLUE))
    try:
        inpu = session.prompt(stylize("Please ", Sty.DEFAULT, "login", Sty.GREEN, " or ", Sty.DEFAULT, "register", Sty.RED, " to continue. (", Sty.DEFAULT, 'l', Sty.GREEN, "/", Sty.DEFAULT, 'r', Sty.RED, "): ", Sty.DEFAULT))
        if inpu == 'r':
            print("Registering new account ...")
            account.username = input("Username: ")
            double_check = True
            while double_check:
                password = getpass.getpass("Password (Hidden): ")
                password2 = getpass.getpass("Confirm Password: ")
                if password == password2:
                    double_check = False
                else:
                    print("Passwords do not match. Try again.")
                    
            account.password = password
            account.email = input("Email: ")  # TODO: Email verification.
            # check if login was valid
            os.system('cls' if os.name == 'nt' else 'clear')
            
            if not account.create_user():
                welcome()
        else:
            account.username = input("Username: ")
            account.password = getpass.getpass("Password (Hidden): ")
            # check if login was valid
            if not account.login_user():
                os.system('cls' if os.name == 'nt' else 'clear')
                print("Invalid login. Try again. Contact admin(s) if you forgot your password.")
                welcome()
    except KeyboardInterrupt:
        exit()
    except EOFError:
        exit()

def title():
    printf(f"RemoteRF Reservation System", Sty.BOLD)
    # printf(f"Logged in as: ", Sty.DEFAULT, f'{account.username}', Sty.MAGENTA)
    printf(f"Input ", Sty.DEFAULT, "'help' ", Sty.BRIGHT_GREEN, "for avaliable commands.", Sty.DEFAULT)  

def commands():
    printf("Commands:", Sty.BOLD)
    printf("'clear' ", Sty.MAGENTA, "- clear terminal", Sty.DEFAULT)
    printf("'getdev' ", Sty.MAGENTA, "- view devices", Sty.DEFAULT)
    printf("'help' or 'h'", Sty.MAGENTA, "- show this help message", Sty.DEFAULT)
    printf("'perms' ", Sty.MAGENTA, "- view permissions", Sty.DEFAULT)
    printf("'exit' or 'quit'", Sty.MAGENTA, "- exit", Sty.DEFAULT)
    printf("'getres' ", Sty.MAGENTA, "- view all reservations", Sty.DEFAULT)
    printf("'myres' ", Sty.MAGENTA, "- view my reservations", Sty.DEFAULT)
    printf("'cancelres' ", Sty.MAGENTA, "- cancel a reservation", Sty.DEFAULT)
    printf("'resdev' ", Sty.MAGENTA, "- reserve a device", Sty.DEFAULT)
    # printf("'resdev -n' ", Sty.MAGENTA, "- naive reserve device", Sty.DEFAULT)
    # printf("'resdev s' ", Sty.MAGENTA, "- Reserve a Device (by single date)", Sty.DEFAULT)
    # check if user is admin
    # if account.get_perms().results['UC'] == 'Admin':
    
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
        
        # grab the reservation
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

# New block scheduling

def fetch_all_reservations():
    data = account.get_reservations()
    if 'ace' in data.results:
        print(f"Error: {unmap_arg(data.results['ace'])}")
        return []
    entries = []

    for key, value in data.results.items():
        parts = unmap_arg(value).split(',')
        # Convert both start and end times to datetime objects.
        entry = {
            'username': parts[0],
            'device_id': int(parts[1]),  # Stored as an int
            'start_time': datetime.datetime.strptime(parts[2], '%Y-%m-%d %H:%M:%S'),
            'end_time': datetime.datetime.strptime(parts[3], '%Y-%m-%d %H:%M:%S')
        }
        entries.append(entry)
    return entries

def fetch_reservations_for_range(start_day: datetime.date, end_day: datetime.date):
    """
    Fetch all reservations (via fetch_all_reservations) and filter those whose start_time date falls between start_day and end_day (inclusive).
    Returns a dictionary keyed by (device_id, day) (device_id as string, day as datetime.date) with a list of reservation tuples.
    """
    all_res = fetch_all_reservations()  # This calls the network only once.
    res_dict = {}
    for res in all_res:
        res_day = res['start_time'].date()
        if start_day <= res_day <= end_day:
            key = (str(res['device_id']), res_day)
            res_dict.setdefault(key, []).append((res['start_time'], res['end_time']))
    return res_dict

def is_slot_conflicting(slot: tuple, reservations: list):
    """Return True if the slot overlaps with any reservation in the provided list."""
    slot_start, slot_end = slot
    for res_start, res_end in reservations:
        if slot_start < res_end and slot_end > res_start:
            return True
    return False

def interactive_reserve_next_days(block_minutes=60):
    """
    Interactive function that:
      1) Displays a menu of all devices (0-based indexing).
      2) Prompts the user for which device they want.
      3) Prompts how many days (starting today) to check for available reservations.
      4) Prompts for an optional block duration in minutes (default = 60).
      5) Displays the free time slots (in 'block_minutes' increments) for that device,
         over the indicated number of days, also 0-based indexed.
      6) Reserves the chosen slot on that device, after confirmation.
    """
    try:
        # --- 1) Fetch and display all devices ---
        data = account.get_devices()
        if 'ace' in data.results:
            print(f"Error: {unmap_arg(data.results['ace'])}")
            return
        
        print("Devices:")
        # Sort devices by integer key
        sorted_device_ids = sorted(data.results.keys(), key=int)
        
        for idx, dev_id in enumerate(sorted_device_ids):
            dev_name = unmap_arg(data.results[dev_id])
            print(f"{idx}. Device ID: {dev_id}   Name: {dev_name}")
        
        # --- 2) Prompt user to pick a device by 0-based index ---
        device_selection = input("Which device do you want? (enter the 0-based index): ")
        try:
            device_selection = int(device_selection)
            if device_selection < 0 or device_selection >= len(sorted_device_ids):
                print("Invalid selection.")
                return
        except ValueError:
            print("Invalid input. Please enter a number.")
            return
        
        chosen_device_id = sorted_device_ids[device_selection]
        
        # --- 3) Prompt user for the number of days ---
        num_days = int(input("Enter the number of days to check for available reservations (starting today): "))
        
        # --- 4) Optionally override block_minutes ---
        # user_block_input = input(f"Enter block duration in minutes (e.g. 15, 30, 60, 120). Press Enter to default ({block_minutes}): ").strip()
        # if user_block_input:
        #     try:
        #         block_minutes = int(user_block_input)
        #     except ValueError:
        #         print("Invalid block duration. Using default of 60 minutes.")
        #         block_minutes = 60
        
        # --- 5) Find all free time slots for the chosen device over the next `num_days` days ---
        
        today = datetime.date.today()
        end_day = today + datetime.timedelta(days=num_days - 1)
        
        # We only need one call to fetch reservations for the range:
        reservations_range = fetch_reservations_for_range(today, end_day)
        
        # We'll keep a list of (day, (slot_start, slot_end)) for which the device is free
        available_slots = []
        
        now = datetime.datetime.now()
        
        # Helper to build time slots of length 'block_minutes' starting at 00:00 up to 24:00
        def build_time_slots(date: datetime.date, block_size: int):
            slots = []
            start_of_day = datetime.datetime.combine(date, datetime.time(0, 0))
            minutes_in_day = 24 * 60  # 1440
            current_offset = 0
            while current_offset < minutes_in_day:
                slot_start = start_of_day + datetime.timedelta(minutes=current_offset)
                slot_end   = slot_start + datetime.timedelta(minutes=block_size)
                # Stop if slot_end bleeds into the next calendar day
                if slot_end.date() != date and slot_end.time() != datetime.time.min:
                    break
                slots.append((slot_start, slot_end))
                current_offset += block_size
            return slots
        
        # Build free slots for each day in [today, end_day]
        for i in range(num_days):
            day = today + datetime.timedelta(days=i)
            all_slots = build_time_slots(day, block_minutes)
            
            # The reservations for the chosen device on this day:
            key = (str(chosen_device_id), day)
            day_reservations = reservations_range.get(key, [])
            
            for slot in all_slots:
                slot_start, slot_end = slot
                # Skip if it's for "today" and the slot ends in the past
                if day == today and slot_end <= now:
                    continue
                
                # Check for conflict
                if not is_slot_conflicting(slot, day_reservations):
                    available_slots.append((day, slot))
        
        if not available_slots:
            print(f"No available time slots for device {chosen_device_id} in the next {num_days} days.")
            return
        
        # Sort by day, then by slot start time
        available_slots.sort(key=lambda x: (x[0], x[1][0]))
        
        # --- Display the available slots, using 0-based index ---
        print(f"\nAvailable time slots for device {chosen_device_id} over the next {num_days} days:")
        last_day = None
        for idx, (day, slot) in enumerate(available_slots):
            slot_start_str = slot[0].strftime('%I:%M %p')
            slot_end_str   = slot[1].strftime('%I:%M %p')
            if day != last_day:
                # Print a header for the day
                day_header = f"{day.strftime('%Y-%m-%d')} ({day.strftime('%a')}) {day.strftime('%b')}. {day.day}"
                print("\n" + day_header)
                last_day = day
            
            print(f"  {idx}. {slot_start_str} - {slot_end_str}")
        
        # Prompt user to pick a slot by 0-based index
        selection = input("Select a slot by index: ")
        try:
            selection = int(selection)
            if selection < 0 or selection >= len(available_slots):
                print("Invalid selection.")
                return
        except ValueError:
            print("Invalid input. Please enter a number.")
            return
        
        chosen_day, chosen_slot = available_slots[selection]
        slot_start_str = chosen_slot[0].strftime('%I:%M %p')
        slot_end_str   = chosen_slot[1].strftime('%I:%M %p')
        
        confirmation = input(
            f"You have selected a reservation on {chosen_day.strftime('%Y-%m-%d')} "
            f"from {slot_start_str} to {slot_end_str} on device {chosen_device_id}. "
            f"Confirm reservation? (y/n): "
        ).strip().lower()
        
        if confirmation != 'y':
            print("Reservation cancelled.")
            return
        
        # print(f"device_id : {chosen_device_id}, start_time : {chosen_slot[0]}, end_time : {chosen_slot[1]}")
        
        # --- 6) Reserve the chosen slot on the chosen device ---
        token = account.reserve_device(int(chosen_device_id), chosen_slot[0], chosen_slot[1])
        if token:
            print(f"Reservation successful on device {chosen_device_id} for "
                  f"{chosen_day.strftime('%Y-%m-%d')} {slot_start_str}-{slot_end_str}.")
            print(f"Thy Token -> {token}")
            print("Please keep this token safe, as it is not saved on server side "
                  "and cannot be retrieved again.")
        
    except Exception as e:
        print(f"Error: {e}")

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
        # elif inpu == "resdev s":
        #     interactive_reserve_all()
        elif inpu == "resdev":
            interactive_reserve_next_days(block_minutes=60) 
        elif inpu == 'cancelres':
            cancel_my_reservation()
        elif inpu == 'resdev -n':
            # check if user is admin
            # if account.get_perms().results['UC'] == 'Admin':
            reserve()
        else:
            print(f"Unknown command: {inpu}")
    except KeyboardInterrupt:
        break
    except EOFError:
        break