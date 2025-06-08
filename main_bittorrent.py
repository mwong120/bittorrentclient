#!/usr/bin/python3
import socket
import select
import argparse
import struct
import time
import threading
import sys
import ipaddress
from collections import deque
import urllib
import queue
from  file_bittorrent import *
from  uploader_bittorrent import *
from downloader_bittorrent import *
from bitarray import bitarray


stop = False # stop variable used to stop all threads
download_queue = queue.Queue() #Queue of files to be downloaded 

class Peer:
    def __init__(self, socket, address, port):
        self.address = address
        self.port = port
        self.socket = socket
        self.id = b""
        self.am_choking = True        # We are choking them
        self.am_interested = False    # We are interested in the peer
        self.peer_choking = True      # Peer is choking us
        self.peer_interested = False  # Peer is interested in us
        self.info_hash = ""           # Info Hash that the peer requested
        self.bitfield = bitarray()




verbose = False

def handle_user_input():
    print("Valid input:\ndownload <filepath.torrent>\ndirect <ip> <port> <filename>\nstop\nhelp\n\n")
    global stop
    while not stop:
        # Read user input from the command line
        user_input = input("> ").strip()

        if user_input.startswith("download"):
            _, torrent_path = user_input.split(maxsplit=1)
            download_queue.put(torrent_path)
            if verbose:
                print(f"Added to download queue: {torrent_path}") #debug

        elif user_input.startswith("direct"): #direct download from ip port from filepath
            print("direct download")
            parts = user_input.split()
            if len(parts) >= 4:
                ip = parts[1]
                port = int(parts[2])
                filepath = ' '.join(parts[3:])  # Concatenate the remaining parts to form the filepath
                download_queue.put((ip, port, filepath))
                print(f"Added to download queue: {filepath}")
        elif user_input == "stop":
            stop = True
        elif user_input == "help":
            print("\n\nValid input:\n> download <filepath.torrent>\n> direct <ip> <port> <filename>\n> stop\n> help\n\n")
        else:
            print("urm, invalid")
    
def downloader_thread():
    while not stop:
        try:
            torrent_file = download_queue.get(timeout=1)  # Wait for a torrent file to be available
        except queue.Empty:
            continue
        
        if isinstance(torrent_file, str):  # Regular download
            print(f"Starting download for: {torrent_file}")
            download_torrent(torrent_file, False)
        elif isinstance(torrent_file, tuple) and len(torrent_file) == 3:  # Direct download
            ip, port, filepath = torrent_file
            print(f"Starting direct download for: {filepath} from {ip}:{port}")
            direct_download(filepath, ip, port, listen_port)
            
def uploader_thread(server_socket):
    while not stop:
        try:
            client_socket, client_address = server_socket.accept()
            print(f"Connection from {client_address}")
            peer = Peer(client_socket, client_address[0], client_address[1])
            peerlist.append(peer)  # Add peer to the global list
            
            # Handle each peer connection in a separate thread
            threading.Thread(target=handle_peer_connection, args=(peer,), daemon=True).start()

        except Exception as e:
            print(f"Uploader encountered an error: {e}")


def main(args):    
    global stop
    #TODO socket should be TCP
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(('localhost', args.p)) #get port from arguments to set up the socket
    server_socket.listen(10) #10 is a random number, probably fine  
    
    
    # Setting up threads for downloading and user input handling
    downloader = threading.Thread(target=downloader_thread,daemon=True)
    downloader.start()

    
    # Start uploader thread
    uploader = threading.Thread(target=uploader_thread, args=(server_socket,), daemon=True)
    uploader.start()
    
    user_input_thread = threading.Thread(target=handle_user_input, args=(), daemon=True)
    user_input_thread.start()   

    #uploader code
    max_message_size = 1024 #random number, should be updated
    sockets_to_poll = [server_socket]

    try:
        while not stop:
            #update sockets_to_poll, can maybe do less often? idk just do all the time :shrug:
            
            
            readable, _, _ = select.select(sockets_to_poll, [], [])

            for ready_socket in readable:
                #new connection
                if ready_socket is server_socket:
                    client_socket, client_address = server_socket.accept()
                    print(f"Connection from {client_address}")
                    peer = Peer(client_socket, client_address[0], client_address[1])
                    peerlist.append(peer)  # Add the peer to the list of connected peers
                    sockets_to_poll.append(client_socket)
                #existing connection
                else:
                    data = ready_socket.recv(max_message_size)
                    if not data:
                        print("Connection closed by peer, empty data")
                        ready_socket.close()
                        sockets_to_poll.remove(ready_socket)
                        continue

                    peer = get_peer_of_socket(ready_socket)
                    if peer != -1:
                        print('found peer, handling')
                        handle_request(peer, data)
                    else:
                        print("could not find peer!!")
                        
    except KeyboardInterrupt:
        print("\n< Exiting.")
        stop = True
        server_socket.close()
        sys.exit()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bittorrent client")
    parser.add_argument("-p", type=int, required=True, help="The port that the client will bind to and listen on")
    
    
    args = parser.parse_args()
    main(args)

