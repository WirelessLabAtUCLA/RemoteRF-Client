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
                password = getpass.getpass("Password (Hidden): ")
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
    printf("'resdev s' ", Sty.MAGENTA, "- Reserve a Device (by single date)", Sty.DEFAULT)
    # check if user is admin
    if account.get_perms().results['UC'] == 'Admin':
        printf("'naiveresdev' ", Sty.MAGENTA, "- Old implementation of reservations", Sty.DEFAULT)
    
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

def fetch_all_devices():
    response = rpc_client(
        function_name='ACC:get_dev',
        args={
            "un": map_arg(account.username),
            "pw": map_arg(account.password)
        }
    )
    devices = []
    # Sort keys using numeric conversion when possible.
    for key in sorted(response.results.keys(), key=lambda k: int(k) if k.isdigit() else k):
        devices.append(key)
    return devices

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

def fetch_device_reservations_by_date(device_id: str, date: datetime.date):
    """
    Filter all reservations to those for a specific device (by ID, as a string)
    on the specified date.
    """
    all_res = fetch_all_reservations()
    device_reservations = []
    for res in all_res:
        # Convert the stored device_id (an int) to a string for comparison.
        if str(res['device_id']) == device_id and res['start_time'].date() == date:
            device_reservations.append((res['start_time'], res['end_time']))
            
    return device_reservations

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

def build_hourly_slots(date: datetime.date, start_hour: int = 0, end_hour: int = 24):
    """Generate 1-hour time slots for the given date."""
    slots = []
    for hour in range(start_hour, end_hour):
        slot_start = datetime.datetime.combine(date, datetime.time(hour, 0))
        slot_end = slot_start + datetime.timedelta(hours=1)
        slots.append((slot_start, slot_end))
    return slots

def is_slot_conflicting(slot: tuple, reservations: list):
    """Return True if the slot overlaps with any reservation in the provided list."""
    slot_start, slot_end = slot
    for res_start, res_end in reservations:
        if slot_start < res_end and slot_end > res_start:
            return True
    return False

def display_free_slots_all(date: datetime.date):
    """
    Display available 1-hour slots aggregated across all devices on a given date.
    A slot is available if at least one device is free.
    Omits any slots whose end time is in the past.
    Returns a tuple (chosen_slot, chosen_device) where chosen_slot is (start_time, end_time)
    and chosen_device is the ID (converted to int if possible) of an available device,
    or (None, None) if no valid selection.
    """
    devices = fetch_all_devices()
    # Build a mapping: device id -> its reservations for the specified date.
    device_reservations = {}
    for dev in devices:
        device_reservations[dev] = fetch_device_reservations_by_date(dev, date)
    
    all_slots = build_hourly_slots(date)
    now = datetime.datetime.now()
    available_slots = {}  # key: slot tuple, value: list of available device IDs
    for slot in all_slots:
        if slot[1] <= now:
            continue
        free_devices = []
        for dev in devices:
            if not is_slot_conflicting(slot, device_reservations[dev]):
                free_devices.append(dev)
        if free_devices:
            available_slots[slot] = free_devices

    if not available_slots:
        print("No available time slots for any device on that day.")
        return None, None

    print("Available time slots (aggregated across devices):")
    sorted_slots = sorted(available_slots.keys())
    for idx, slot in enumerate(sorted_slots):
        start_str = slot[0].strftime('%I:%M %p')
        end_str = slot[1].strftime('%I:%M %p')
        num_available = len(available_slots[slot])
        print(f"{idx + 1}: {start_str} - {end_str} (Devices available: {num_available})")
    
    try:
        selection = int(input("Select a slot by number: "))
        if selection < 1 or selection > len(sorted_slots):
            print("Invalid selection.")
            return None, None
    except ValueError:
        print("Invalid input. Please enter a number.")
        return None, None

    chosen_slot = sorted_slots[selection - 1]
    # Automatically choose one device.
    candidate = sorted(available_slots[chosen_slot])[0]
    try:
        chosen_device = int(candidate)
    except ValueError:
        chosen_device = candidate
    return chosen_slot, chosen_device

def interactive_reserve_all():
    """
    Interactive function that prompts for a reservation date,
    displays aggregated free 1-hour slots (with an accurate count of available devices),
    and reserves the chosen slot on one available device.
    """
    try:
        date_input = input("Enter the date for reservation (YYYY-MM-DD): ")
        reservation_date = datetime.datetime.strptime(date_input, '%Y-%m-%d').date()

        chosen_slot, chosen_device = display_free_slots_all(reservation_date)
        if chosen_slot is None:
            return

        token = account.reserve_device(chosen_device, chosen_slot[0], chosen_slot[1])
        if token != '':
            print(f"Reservation successful on device {chosen_device}. Thy Token -> {token}")
            print("Please keep this token safe, as it is not saved on server side, and cannot be regenerated/reretrieved.")
    except Exception as e:
        print(f"Error: {e}")

def display_free_slots_next_days(num_days: int):
    """
    Display available 1-hour slots aggregated across all devices for the next num_days (starting today).
    A slot is available if at least one device is free (i.e. not reserved during that slot).
    Omits any slots whose end time is in the past (for today).
    Returns a tuple (chosen_day, chosen_slot, chosen_device) where:
      - chosen_day is a datetime.date for the reservation day,
      - chosen_slot is a tuple (start_time, end_time), and
      - chosen_device is one available device for that slot.
    If no slot is available, returns (None, None, None).
    """
    today = datetime.date.today()
    devices = fetch_all_devices()
    now = datetime.datetime.now()
    
    # Compute the end day (inclusive)
    end_day = today + datetime.timedelta(days=num_days - 1)
    # Fetch all reservations for the entire range (one network call)
    reservations_range = fetch_reservations_for_range(today, end_day)
    
    available_slots = []  # List of tuples: (day, slot, free_devices)
    
    for i in range(num_days):
        day = today + datetime.timedelta(days=i)
        all_slots = build_hourly_slots(day)
        for slot in all_slots:
            # If the day is today, skip slots that have already ended.
            if day == today and slot[1] <= now:
                continue
            free_devices = []
            for dev in devices:
                # Look up reservations for this device on this day.
                key = (dev, day)
                current_res = reservations_range.get(key, [])
                if not is_slot_conflicting(slot, current_res):
                    free_devices.append(dev)
            if free_devices:
                available_slots.append((day, slot, free_devices))
    
    if not available_slots:
        print(f"No available time slots for any device in the next {num_days} days.")
        return None, None, None

    # Sort by day and slot start time.
    available_slots.sort(key=lambda x: (x[0], x[1][0]))
    
    print(f"Available time slots for the next {num_days} days:")
    last_day = None
    for idx, (day, slot, free_devices) in enumerate(available_slots):
        # When a new day starts, print a header line with the date and formatted day.
        if last_day is None or day != last_day:
            # Format: YYYY-MM-DD (Abbreviated Weekday) AbbrevMonth. day
            # Example: "2025-02-04 (Tue) Feb. 4"
            day_header = f"{day.strftime('%Y-%m-%d')} ({day.strftime('%a')}) {day.strftime('%b')}. {day.day}"
            print("\n" + day_header)
            last_day = day
        start_str = slot[0].strftime('%I:%M %p')
        end_str = slot[1].strftime('%I:%M %p')
        print(f"  {idx+1}: {start_str} - {end_str} (Devices available: {len(free_devices)})")
    
    try:
        selection = int(input("Select a slot by number: "))
        if selection < 1 or selection > len(available_slots):
            print("Invalid selection.")
            return None, None, None
    except ValueError:
        print("Invalid input. Please enter a number.")
        return None, None, None

    chosen_day, chosen_slot, free_devices_for_slot = available_slots[selection - 1]
    candidate = sorted(free_devices_for_slot)[0]
    try:
        chosen_device = int(candidate)
    except ValueError:
        chosen_device = candidate
    return chosen_day, chosen_slot, chosen_device


def interactive_reserve_next_days():
    """
    Interactive function that prompts the user for the number of days (starting today) to check for available reservations.
    It displays aggregated free 1-hour slots over that period and reserves the chosen slot on one available device,
    after confirming with the user.
    """
    try:
        num_days = int(input("Enter the number of days to check for available reservations (starting today): "))
        chosen_day, chosen_slot, chosen_device = display_free_slots_next_days(num_days)
        if chosen_slot is None:
            return

        start_time = chosen_slot[0].strftime('%I:%M %p')
        end_time = chosen_slot[1].strftime('%I:%M %p')
        confirmation = input(f"You have selected a reservation on {chosen_day.strftime('%Y-%m-%d')} from {start_time} to {end_time} on device {chosen_device}. Confirm reservation? (y/n): ").strip().lower()
        if confirmation != 'y':
            print("Reservation cancelled.")
            return

        token = account.reserve_device(chosen_device, chosen_slot[0], chosen_slot[1])
        if token != '':
            print(f"Reservation successful on device {chosen_device} for {chosen_day.strftime('%Y-%m-%d')}. Thy Token -> {token}")
            print("Please keep this token safe, as it is not saved on server side, and cannot be regenerated/reretrieved.")
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
        elif inpu == "resdev s":
            interactive_reserve_all()
        elif inpu == "resdev":
            interactive_reserve_next_days()
        elif inpu == 'cancelres':
            cancel_my_reservation()
        elif inpu == 'naiveresdev':
            # check if user is admin
            if account.get_perms().results['UC'] == 'Admin':
                reserve()
        else:
            print(f"Unknown command: {inpu}")
    except KeyboardInterrupt:
        break
    except EOFError:
        break