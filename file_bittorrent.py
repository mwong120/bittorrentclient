#!/usr/bin/python3
import os
import threading
from pathlib import Path
import hashlib
from alive_progress import alive_bar
#this file defines file i/o funcitons

lock = threading.Lock()

verbose = True

def delete_files(file_list):
    with lock:
        for file_path in file_list:
            if os.path.exists(file_path):
                os.remove(file_path)
            else:
                print(f"invalid path: {file_path}")


#funtion appends data to file_path if file exists, otherwise, creates file with data
'''def append_to_file(data, file_path):
    mode = 'a' if isinstance(data, str) else 'ab' #a is append (str), ab is append binary
    
    with lock:
        if mode == 'a' and not os.path.exists(file_path):  # if str and file does not exist, create it (mode w)
            mode = 'w'
        with open(file_path, mode) as file:
            file.write(data)'''
            
def append_to_file(data, file_path):
    with lock:
        if os.path.exists(file_path): # file exists, append to it
            with open(file_path, "ab") as file:
                file.write(data)
        else:                         # file does not exist, create it and write to it
            with open(file_path, "wb") as file:
                file.write(data)

#turns assembles files from parts to a piece, or pieces to a whole
#takes in a list of file paths, writes to target_path
#list should be organized in order, so piece 0 then 1 then 2 etc
def assemble(file_list, target_path):
    print("assembling")
    with lock:
        if os.path.exists(target_path):
            print("Replacing a file! beware!")
        else:
            print("Could not open path")
        
        with open(target_path, "ab") as target:  # Using "ab" for binary append mode
            with alive_bar(total=len(file_list), title='Assembling Files') as bar:    
                for file_path in file_list:
                    #print("path:", file_path)
                    bar()
                    if os.path.exists(file_path):
                        try:
                            with open(file_path, "rb") as read_from:
                                interval = Path(file_path).stat().st_size
                                while interval:
                                    chunk = read_from.read(min(4096, interval))  # Read in chunks of 4KB
                                    if not chunk:
                                        break
                                    target.write(chunk)
                                    interval -= len(chunk)
                        except Exception as e:
                            print(f"Error reading file '{file_path}': {e}")
                    else:
                        print(f"Could not find '{file_path}', skipping")

    # List has been assembled to target_path
    # Now, delete all old files
    delete_files(file_list)

'''def assemble(file_list, target_path):
    with lock:
        if os.path.exists(target_path):
            print("Replacing a file! beware!")
        target = open(target_path, "a") #replaces current file if it exists

        for file_path in file_list:
            if(os.path.exists(file_path)):
                read_from = open(file_path, "rb")
                interval = Path(file_path).stat().st_size #should return length of file
                towrite = read_from.read(interval)
                target.write(towrite)
            else:
                print(f"could not find {file_path}, skipping")
    
    #list has been assembled to target_path
    #now, delete all old files
    delete_files(file_list)'''

#does file reading, give it a file, it gives you a string
def file_to_string(file_path):
    with lock:
        if os.path.exists(file_path):
            with open(file_path, "r") as read_from:
                ret = read_from.read()
            return ret #should return whole file as a string
        else:
            return None
        
def make_directory(directory):
    with lock:
        os.makedirs(directory, exist_ok=True)
        return True
    return False


def pront(*text):
    if verbose:
        print(text)