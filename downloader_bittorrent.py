#!/usr/bin/python3import socket
from bencode import bdecode, bencode
from urllib.parse import urlencode, urlparse
import hashlib
import struct
import os
import sys
import errno
import file_bittorrent
import main_bittorrent
import uploader_bittorrent
from enum import Enum
import select
from bitarray import bitarray
import math
import time
import random
from alive_progress import alive_bar
import re

#this file will be run in a process
#Get torrent files from main - use queue?
#Download 1 file at a time, use queue to store what torrents to get next
#Interact with tracker to get peers
#Interact with peers to get pieces
#Get lock, write pieces
#Compile pieces once all received
#Get lock, write compiled file

class Message_Type(Enum):
    CHOKE = 0
    UNCHOKE = 1
    INTERESTED = 2
    NOT_INTERESTED = 3
    HAVE = 4
    BITFIELD = 5
    REQUEST = 6
    PIECE = 7
    CANCEL = 8
    PORT = 9

verbose = True


def create_socket_connection(url):
    # Parse the provided URL to extract hostname and port number
    parsed_url = urlparse(url)
    host = parsed_url.hostname
    port = parsed_url.port if parsed_url.port else 80  # Use port 80 as default if no port is specified

    # Create a new socket using IPv4 and TCP
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    
    # Establish a connection to the specified host and port
    client_socket.connect((host, port))
    return client_socket

def send_http_request(client_socket, url, query_params):
    # Parse the URL to extract the path for the HTTP request
    parsed_url = urlparse(url)
    # URL-encode the query parameters to properly format the GET request query string
    query_string = urlencode(query_params)
    
    # Prepare the HTTP GET request line with the path and query string
    request_line = f"GET {parsed_url.path}?{query_string} HTTP/1.1\r\n"
    
    # Prepare HTTP headers to be sent with the request
    headers = f"Host: {parsed_url.hostname}:{parsed_url.port}\r\nUser-Agent: downloader_bittorrent\r\nAccept-Encoding: gzip, deflate\r\nConnection: keep-alive\r\n\r\n" # Include connection close header to signal end of communication

   # Prepare the full HTTP request by concatenating the request line and headers
    full_request = request_line + headers
    
    # Print the full HTTP request for debugging
    pront("Sending HTTP Request to Tracker:")
    pront(full_request)
    
    # Send the composed HTTP GET request to the server through the socket
    client_socket.sendall(full_request.encode())

def read_http_response(client_socket):
    # Initialize an empty bytes object to accumulate the response
    response = b""
    
    # Continuously read data from the socket in 4096-byte blocks
    while True:
        chunk = client_socket.recv(4096)
        if not chunk:
            break
        response += chunk
    
    # Close the socket once the entire response has been received
    client_socket.close()

    # Print raw response for debugging
    #print("Raw response:", response)
    
    # Split the response into headers and body, ignoring headers for now
    headers, _, body = response.partition(b'\r\n\r\n')
    return body

def parse_tracker_response(response):
    # Decode the bencoded response from the tracker to extract data
     # Print the body to be decoded for debugging
    #print("Response body to be decoded:", response)
    peers = []
    data = bdecode(response)
    if 'peers' in data and isinstance(data['peers'], bytes):
        # If 'peers' is in compact format
        peer_bytes = data['peers']
        num_peers = len(peer_bytes) // 6
        for i in range(num_peers):
            start_index = i * 6
            ip_bytes = peer_bytes[start_index:start_index + 4]
            port_bytes = peer_bytes[start_index + 4:start_index + 6]
            ip = '.'.join(str(byte) for byte in ip_bytes)
            port = struct.unpack('>H', port_bytes)[0]
            peers.append((ip, port))
    else:
        peer_list = data['peers']
        for peer in peer_list:
            ip = peer.get('ip')
            port = peer.get('port')
            if ip and port:
                peers.append((ip, port))
    return peers

def connect_to_peer(peer_info):
    timeout=1
    
    peer_ip, peer_port = peer_info
    pront(f"connecting to  {peer_ip}:{peer_port} ")
    peer_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    peer_socket.settimeout(timeout)
    try:
        peer_socket.connect((peer_ip, peer_port))
        pront(f"Connected to {peer_ip}:{peer_port}")
        peer = main_bittorrent.Peer(peer_socket,peer_ip,peer_port)
        return peer
    except socket.timeout:
        pront(f"Timeout occurred trying to connect to {peer_ip}:{peer_port}")
        return None
    except Exception as e:
        pront(f"Failed to connect to {peer_ip}:{peer_port} with error {e}")
        return None

def shake_peer_hand(peer, info_hash):
    my_id = b"-BT0001-123456789012" #Used the  Azureus-style, we can change this later 
       
    protocol_name = b"BitTorrent protocol"
    reserved_bytes = b"\x00" * 8

    # Construct the handshake message
    handshake_msg = (
        len(protocol_name).to_bytes(1, byteorder='big') +
        protocol_name +
        reserved_bytes +
        info_hash +
        my_id
    )
    try:
        peer.socket.sendall(handshake_msg)
        pront("sent handshake")

        ready_to_read, _, _ = select.select([peer.socket], [], [], 5) #timeout = 5? can change :shrug:
        if ready_to_read:
            response = peer.socket.recv(68)  # Expected length of a correct handshake response
            pront("Handshake response received")
            return True
        else:
            print("Connection timed out while waiting for a handshake response")
            return False

    except Exception as e:
        print(f"Error during handshake: {e}")
        return False

'''
    def request_piece_from_peer(peer_socket, piece_index, begin, piece_length, block_size=16384):
    # Request a specific piece from a peer
     # The message structure is: <length><message_id><index><begin><length>
     # Break down the piece request into multiple block requests
    num_blocks = (piece_length + block_size - 1) // block_size
    for i in range(num_blocks):
        block_begin = begin + i * block_size
        block_length = min(block_size, piece_length - block_begin)
        request_message = struct.pack('!IbIII', 13, 6, piece_index, block_begin, block_length)
        try:
            peer_socket.socket.sendall(request_message)
            pront("Requested block: ", block_begin, " of piece: ", piece_index)
        except Exception as e:
            print(f"Error requesting block from peer, Error: {e}")
            if peer_socket:
                peer_socket.socket.close()
            return -1'''
#                                 socket,      index      index start, length of pieces, block size, length of final segment
def request_final_piece_from_peer(peer, piece_index, piece_length, remaining_bytes, block_size=16384):
    pront("Leftover bytes:", remaining_bytes)
    begin = 0
    # Request a specific piece from a peer
     # The message structure is: <length><message_id><index><begin><length>
     # Break down the piece request into multiple block requests
<<<<<<< HEAD
    num_blocks = (piece_length + block_size - 1) // block_size
    num_blocks = min((piece_length + block_size - 1) // block_size, math.ceil(remaining_bytes/block_size))
    for i in range(num_blocks):
        block_begin = begin + i * block_size
        block_length = min(block_size, piece_length - block_begin, remaining_bytes - block_begin)
        if min(block_size, piece_length - block_begin, remaining_bytes - block_begin) == remaining_bytes - block_begin:
            print("MINIMUM IS LEFTOVER BYTES, REQUESTING LAST SEGMENT, LEN:", remaining_bytes - block_begin)
=======
    #num_blocks = (piece_length + block_size - 1) // block_size
    num_blocks = min((piece_length + block_size - 1) // block_size, math.ceil(leftover_bytes/block_size))
    for i in range(num_blocks):
        block_begin = begin + i * block_size
        block_length = min(block_size, piece_length - block_begin, leftover_bytes - block_begin)
        
        #if min(block_size, piece_length - block_begin, leftover_bytes - block_begin) == leftover_bytes - block_begin:
        if block_length == leftover_bytes - block_begin:
            print("Requesting last segment, len:", block_length)
            print("MINIMUM IS LEFTOVER BYTES, REQUESTING LAST SEGMENT, LEN:", leftover_bytes - block_begin)
>>>>>>> 511db6cf8a93ec4975fba99827f15f71c5394814
        request_message = struct.pack('!IbIII', 13, 6, piece_index, block_begin, block_length)
        try:
            peer.socket.sendall(request_message)
            pront("Requested block: ", block_begin, "length: ", block_length, " of piece: ", piece_index)
        except Exception as e:
            print(f"Error requesting block from peer, Error: {e}")
            if peer:
                peer.socket.close()
            return -1

def request_all_available(peer, piece_length, remaining_bytes):

<<<<<<< HEAD
    available_pieces = [index for index, value in enumerate(peer.bitfield) if value]
    random.shuffle(available_pieces)
    for available_piece in available_pieces[:3]:
        print("Requesting all available from peer:", peer.address, " Requesting: ", available_piece)
        request_final_piece_from_peer(peer, available_piece, piece_length, remaining_bytes, block_size=16384)


=======
>>>>>>> 511db6cf8a93ec4975fba99827f15f71c5394814
def receive_piece_from_peer(peer):
    piece_data = b''  # Buffer to hold the piece data
    piece_index = None
    try:
        while True:
            header = peer.socket.recv(4)
            if not header:
                print("No data received; peer might have closed the connection.")
                break

            message_length = struct.unpack('!I', header)[0]
            if message_length == 0:
                print("Keep-alive message received, no data to process.")
                continue

            message_data = peer.socket.recv(message_length)
            message_id = message_data[0]
            if message_id != 7:  # ID 7 is for 'piece' messages
                print(f"Unexpected message ID: {message_id}, expected 'piece' message.")
                continue

            temp_piece_index, begin = struct.unpack('!II', message_data[1:9])
            block = message_data[9:]
            if piece_index is None:
                piece_index = temp_piece_index
            if temp_piece_index != piece_index:
                print("Error: Received blocks from different pieces.")
                return None, None
            piece_data += block
            pront(f"Received block, begin at {begin}, size of block: {len(block)}.")

            # Check if the piece is complete
            if len(piece_data) == peer.piece_length[piece_index]:
                break
    except Exception as e:
        print(f"Error receiving piece from peer: {e}")
        return None, None

    return piece_index, piece_data


def save_piece_to_storage(piece_data, piece_index, piece_directory):
    # Save received piece data to storage
    #piece_directory='downloaded_pieces'
    #print(f"Attempting to save piece {piece_index}...")
    piece_path = os.path.join(piece_directory, f"piece_{piece_index}.bin")
    file_bittorrent.append_to_file(piece_data, piece_path)
    pront(f"Piece {piece_index} saved successfully to {piece_path}.")
    return piece_path

def dissect_pieces(data):
    #length needs to be %20
    if len(data) % 20 != 0:
        return -1
    
    pieces = {}
    #for each piece, add it to pieces
    for i in range(len(data) // 20):
        piece = data[i*20 : (i+1)*20]
        hex_piece = piece
        pieces[i] = hex_piece
        #pront(f"pieces[{i}]={pieces[i]}")
    return pieces

#direct download from ip, port
def direct_download(torrent_file, ip, port):
    print(f"ip,port is {(ip,port)}")
    peerinfo = (ip, port)
    #peer_id = "-BT0001-123456789012" #Used the  Azureus-style, we can change this later 
    #download_from_peers([peerinfo], info_hash)
    download_torrent(torrent_file, [peerinfo])


def download_torrent(torrent_file_path, isdirect):
    #print(f"torrent file path {torrent_file_path}")
    if os.path.exists(torrent_file_path) == False:
        print("File Does Not Exist", file=sys.stderr)
        return
    # Open and read the .torrent file, then decode its contents
    with open(torrent_file_path, 'rb') as file:
        file_content = file.read()
    try:
        torrent_data = bdecode(file_content)
        #print(torrent_data)  # Debug print to check keys and content
        #print("Available keys:", list(torrent_data.keys())) 
    except Exception as e:
        print(f"Failed to decode torrent file: {e}")
        return
    
    # Determine if this is a single-file or multi-file torrent
    if 'files' in torrent_data['info']:
        # Multi-file torrent
        multi_file_mode = True
        files_info = torrent_data['info']['files']
        total_size = sum(file['length'] for file in files_info)
    else:
        # Single-file torrent
        multi_file_mode = False
        total_size = torrent_data['info']['length']
        files_info = [{'length': total_size, 'path': [torrent_data['info']['name']]}]

    if torrent_data['info']['name']:
        final_file_name = torrent_data['info']['name']
    else:
        final_file_name = "<ERROR, COULD NOT GET FILENAME>"

    print(f"Starting download for {final_file_name}")

    piece_length = torrent_data['info']['piece length'] 
    pront("Piece Length:",piece_length)

    pront("Total length in bytes: ", total_size)

    


    number_of_pieces = math.ceil(total_size / piece_length)
    leftover_bytes = total_size % piece_length
    print("Leftover bytes:", leftover_bytes)
    #number_of_pieces = math.floor(total_size / piece_length)
    name = torrent_data['info']['name']

    remaining_bytes = total_size
    pront("number of pieces: ", number_of_pieces)

    piece_hashes = dissect_pieces(torrent_data['info']['pieces'])
    pront("Number of Piece hashes:", len(piece_hashes))


    # Determine if the key exists in string or bytes form and retrieve it 
    announce_key = 'announce' if 'announce' in torrent_data else b'announce' if b'announce' in torrent_data else None
    if announce_key is None:
        print("Torrent file does not contain an 'announce' key.")
        return
    try:
        tracker_url = torrent_data[announce_key]
        if isinstance(tracker_url, bytes):
            tracker_url = tracker_url.decode('utf-8')
        #print("Tracker URL:", tracker_url)
    except KeyError:
        print("Torrent file does not contain an 'announce' key.")
        print("Available keys:", list(torrent_data.keys()))
        return
    except Exception as e:
        print(f"Error processing tracker URL: {e}")
        return
    
    # Create a unique peer ID for this client and prepare the query parameters for the tracker request
    peer_id = "-BT0001-123456789012" #Used the  Azureus-style, we can change this later 
    info_encoded = bencode(torrent_data['info'])
    info_hash = hashlib.sha1(info_encoded).digest()
    query_params = {
        'info_hash': info_hash,  # Hash of the info section of the torrent file, representing the file identity
        'peer_id': peer_id,
        'port': 6881,  # The port this client is listening on for incoming connections We can change this later if needed
        'uploaded': 0,  # Initially no data has been uploaded
        'downloaded': 0,  # Initially no data has been downloaded
        'left': total_size,  # Total size of the file left to download
        'compact': 1,
        'event': 'started'  # Event type sent to the tracker
    }

    if isdirect is False:
        # Connect to the tracker, send the request, and read the response
        tracker_socket = create_socket_connection(tracker_url)
        send_http_request(tracker_socket, tracker_url, query_params)
        
        response = read_http_response(tracker_socket)
        
        # Parse the list of peers received from the tracker and print it
        #either print or use the actual list returned from the tracter
        peers = parse_tracker_response(response)
    else:
        peers = isdirect

    print(f"List of {len(peers)} peers")
    
    
    download_directory = 'downloaded_pieces'
    
    retry_counts = {}
    downloaded_all_bits = False
    connected_peers = []
    downloaded_pieces = bitarray(number_of_pieces) #Bitarray to keep track of pieces we have successfully downloaded
    #downloaded_pieces = [False] * number_of_pieces
    piece_index = 0 # Assuming we start with the first piece
    block_size = 16384
    file_bittorrent.make_directory(download_directory)
        
    #piece_blocks_received = [set() for _ in range(number_of_pieces)]  # Tracks received blocks for each piece
    piece_blocks_received = {}
    pieces_saved = set()
    pieces_needed = {x for x in range(number_of_pieces)}


    with alive_bar(total=len(peers), title='Connecting to peers') as bar1:#pretty loading bar
        for peer in peers:
            bar1()#pretty loading bar
            peer_socket = connect_to_peer(peer)
            if peer_socket is not None:    
                connected_peers.append(peer_socket)
    with alive_bar(total=len(connected_peers), title='Shaking Peer hands') as bar2:      #pretty loading bar     
        for peer in connected_peers:
            shake_peer_hand(peer,info_hash)
            bar2()#pretty loading bar

    list_of_files = []
    with alive_bar(total=number_of_pieces, title='Downloading Files') as bar: #pretty loading bar

        '''file_bittorrent.make_directory(download_directory)
        downloaded_pieces = [False] * number_of_pieces  # Track downloaded pieces
        piece_blocks_received = [set() for _ in range(number_of_pieces)]  # Tracks received blocks for each piece
        '''


        #while not all(downloaded_pieces):  # Continue until all pieces are downloaded 
        while not downloaded_all_bits:
            #Code for handling sockets and processing messages
            sockets_for_poll = [peer.socket for peer in connected_peers]
            readable, _, _ = select.select(sockets_for_poll, [], [], 3)
            for ready_socket in readable:
                data = read_from_socket(ready_socket)
                #print("From:",peer.address,  "Raw response:", data)
                peer = get_peer_from_socket(ready_socket,connected_peers)
                if data:
                    messages = get_messages(peer,data)
                    for message in messages:
                        status, info = process_message(peer,message,download_directory, piece_hashes, piece_length, remaining_bytes)
                    
                        if status == 1 and info and info['piece_index'] not in pieces_saved:
                            piece_index = info['piece_index']
                            block_data = info['block_data']
                            block_begin = info['block_begin']
                            block_length = len(block_data)
                            
                            # Store received block
                            ''' 
                            if block_begin not in piece_blocks_received[piece_index]:
                                piece_blocks_received[piece_index][block_begin] = block_data
                            '''
                            

                            if piece_index not in piece_blocks_received:
                                pront("dont have it yet, make a new dictionary")
                                piece_blocks_received[piece_index] = {}  # Initialize a new dictionary for this piece
                            piece_blocks_received[piece_index][block_begin] = block_data

                            pront("Index of received piece is:", piece_index)
                            #pront("length of blocks is: ", len(piece_blocks_received[piece_index]))

                            sum_blocks = 0
                            for block in piece_blocks_received[piece_index]:
                                sum_blocks += len(piece_blocks_received[piece_index][block])
                                #pront("Length of block is:", len(piece_blocks_received[piece_index][block]))

                            # Verify and assemble piece if all blocks received
                            #if piece_index == number_of_pieces-1:
                            #    print("remaining bytes:", remaining_bytes)

                            #if len(piece_blocks_received[piece_index]) * block_length >= piece_length or (piece_index == number_of_pieces-1):
                            if sum_blocks >= piece_length or sum_blocks >= remaining_bytes:
                                pront("Finsihed, checking hash")
                                ordered_blocks = sorted(piece_blocks_received[piece_index].items())
                                assembled_piece = b''.join(block_data for _, block_data in ordered_blocks)
                                
                                
                                result = verify_piece_hash(assembled_piece, piece_hashes[piece_index])


                                #if result is not True:
                                #    assembled_piece = b"".join(piece_blocks_received[piece_index][k] for k in sorted(piece_blocks_received[piece_index]))
                                #    result = verify_piece_hash(assembled_piece, piece_hashes[piece_index])
                                #else:
                                #    print("WORKED THE FIRST TIME WORKED THE FIRST TIME WORKED THE FIRST TIME WORKED THE FIRST TIME WORKED THE FIRST TIME WORKED THE FIRST TIME WORKED THE FIRST TIME ")

                                
                                if result:
                                    save_piece_to_storage(assembled_piece, piece_index, download_directory)
                                    downloaded_pieces[piece_index] = True
                                    print("Verified ", piece_index)
                                    pieces_saved.add(piece_index)
                                    bar()
                                    pieces_needed.remove(piece_index)
                                    if pieces_needed:
                                        piece_index = min(pieces_needed)
                                    else:
                                        downloaded_all_bits = True

                                    remaining_bytes = remaining_bytes - piece_length
                                    print("Remaining bytes updated:", remaining_bytes)
                                    # Move to the next piece if possible
                                    #if piece_index < number_of_pieces - 1:
                                    #    piece_index += 1   
                                else:
<<<<<<< HEAD
                                    print(f"Hash verification failed for piece {piece_index}. Retrying...")
                                    piece_blocks_received[piece_index].clear()
                                    result = request_final_piece_from_peer(peer, piece_index, piece_length, remaining_bytes, block_size)
                                    if result == -1:
                                        print("Failed to request piece from peer, aborting.")
                                        connected_peers.remove(peer)
                                        print("Removed peer from list, total connected peers:", len(connected_peers))
=======
                                    # Hash verification failed, check retry count
                                    if piece_index not in retry_counts:
                                        retry_counts[piece_index] = 0
                                    retry_counts[piece_index] += 1
                                    
                                    if retry_counts[piece_index] <= 5:
                                        print(f"Hash verification failed for piece {piece_index}. Retrying...")
                                        piece_blocks_received[piece_index].clear()
                                        result = request_final_piece_from_peer(peer, piece_index, 0, piece_length, remaining_bytes, block_size)
                                        if result == -1:
                                            print("Failed to request piece from peer, aborting.")
                                            connected_peers.remove(peer)
                                            print("Removed peer from list, total connected peers:", len(connected_peers))
>>>>>>> 511db6cf8a93ec4975fba99827f15f71c5394814
                        elif status == -1:
                                # If a hash mismatch or any other error, re-request the entire piece
                                print(f"Error with piece {info['piece_index']}, re-requesting...")
                                result = request_final_piece_from_peer(peer, piece_index, piece_length, remaining_bytes, block_size)
                                if result == -1:
                                    print("Failed to request piece from peer, aborting.")
                                    connected_peers.remove(peer)
                                    print("Removed peer from list, total connected peers:", len(connected_peers))
                                    
            #If no choked peers cannot send requests        
            if not has_unchoked(connected_peers):
                time.sleep(1)
                print("No Unchoked Peers")
                continue
            

            #piece_index = min(pieces_needed)
            pront("Piece Index Requesting:",piece_index, "/", number_of_pieces-1)

            if pieces_needed:
                pieces_needed = sorted(pieces_needed)
            
            #Code to send requests to peers
            if not downloaded_all_bits:
<<<<<<< HEAD
                redo = True
                while redo:
                    redo = False

                    lowest10 = pieces_needed
                    random.shuffle(lowest10)
                    for x in lowest10[:10]:
                        
                        peer = get_random_peer_having_piece(connected_peers,x,number_of_pieces)
                        if not peer:
                            print(f"No available peers with the required piece:{x}, will retry.")
                            continue
                        result = request_final_piece_from_peer(peer, piece_index, piece_length, remaining_bytes, block_size)
                        if result == -1:
                            print("Failed to request piece from peer, aborting.")
                            connected_peers.remove(peer)
                            print("Removed peer from list, total connected peers:", len(connected_peers))
                            redo = True
                            #return -1

                    if pieces_needed:
                        piece_index = min(pieces_needed)
                    #if piece_index < number_of_pieces - 1:
                    #   piece_index+=1
                    else:
                        downloaded_all_bits = True
            
            '''            
            #Code to send requests to peers
            if not downloaded_all_bits:
                redo = True
                while redo:
                    redo = False
                        
                    peer = get_random_peer_having_piece(connected_peers,piece_index,number_of_pieces)
                    if not peer:
                        print(f"No available peers with the required piece:{piece_index}, will retry.")
                        continue
                    result = request_final_piece_from_peer(peer, piece_index, 0, piece_length, remaining_bytes, block_size)
                    if result == -1:
                        print("Failed to request piece from peer, aborting.")
                        connected_peers.remove(peer)
                        print("Removed peer from list, total connected peers:", len(connected_peers))
                        redo = True
                        #return -1

                    if pieces_needed:
                        piece_index = min(pieces_needed)
                    #if piece_index < number_of_pieces - 1:
                    #   piece_index+=1
                    else:
                        downloaded_all_bits = True
            '''

    list_of_files = [os.path.join(download_directory, f) for f in os.listdir(download_directory)]
    #file_bittorrent.assemble(file_list, final_file_name)
    #sorted(list_of_files)
=======
                connected_peers = send_requests_to_peers(pieces_needed, connected_peers, number_of_pieces, piece_length, remaining_bytes, block_size, 3)
    
>>>>>>> 511db6cf8a93ec4975fba99827f15f71c5394814
    list_of_files = sorted(list_of_files, key=extract_piece_index)
    pront(list_of_files)
    file_bittorrent.assemble(list_of_files, final_file_name)
    print("downloaded ", final_file_name)

def send_requests_to_peers(pieces_needed, connected_peers, number_of_pieces, piece_length, remaining_bytes, block_size, numrequests):
    if pieces_needed:
            piece_index = min(pieces_needed)
    else:
        return connected_peers
    '''
    redo = True
    while redo:
        redo = False

        lowest = list(pieces_needed)
        for x in lowest[:numrequests]:
            print("requesting piece ", x)
            peer = get_random_peer_having_piece(connected_peers,x,number_of_pieces)
            if not peer:
                print(f"No available peers with the required piece:{x}, will retry.")
                continue
            result = request_final_piece_from_peer(peer, x, 0, piece_length, remaining_bytes, block_size)
            if result == -1:
                print("Failed to request piece from peer, aborting.")
                connected_peers.remove(peer)
                print("Removed peer from list, total connected peers:", len(connected_peers))
                redo = True
                #return -1
    return connected_peers
    '''
    for x in sorted(pieces_needed)[:numrequests]:
        print("Requesting piece", x)
        peer = get_random_peer_having_piece(connected_peers, x, number_of_pieces)
        if not peer:
            print(f"No available peers with the required piece: {x}.")
            continue
        result = request_final_piece_from_peer(peer, x, 0, piece_length, remaining_bytes, block_size)
        if result == -1:
            print("Failed to request piece from peer, removing peer.")
            connected_peers.remove(peer)
            print("Removed peer from list, total connected peers:", len(connected_peers))
            continue
    return connected_peers

   
def extract_piece_index(file_path):
    # Use regular expression to extract the piece index from the file path
    match = re.search(r'piece_(\d+)\.bin', file_path)
    if match:
        return int(match.group(1))
    else:
        return None


def verify_piece_hash(piece_data, expected_hash):
    actual_hash = hashlib.sha1(piece_data).digest()
    if actual_hash == expected_hash:
        pront(f"Hashed Correctly")
        return True
    else:
        pront(f"Expected hash: {expected_hash.hex()}, but got {actual_hash.hex()}")
        return False  

  
def get_peer_from_socket(socket,peers):
    for peer in peers:
        if peer.socket is socket:
            return peer
    return -1

#Organizes the multiple messages in the buffer into individual messages for easier processing
def get_messages(peer,data):
    messages = []
    while len(data) > 4:
        message_length, = struct.unpack(">I", data[:4])
        if message_length == 0:
            print(f"Received Keep Alive From {peer.address}:{peer.port}")
            data = data[4:]
            continue
        total_length = message_length + 4
        if len(data) < total_length:
            break
        message = data[:total_length]
        data = data[total_length:]
        messages.append(message)
        #print("total length of received message is ", total_length)
    return messages

#Processes an individual message
def process_message(peer, data, download_directory, piece_hashes, piece_length, remaining_bytes):    
    payload_length, message_id, = struct.unpack(">IB", data[:5])
    if (message_id == Message_Type.CHOKE.value):
        peer.peer_choking = True
        pront(f"Choking Message From {peer.address}:{peer.port}")
        return 0 , 0
    
    elif (message_id == Message_Type.UNCHOKE.value):
        peer.peer_choking = False
        pront(f"Unchoking Message From {peer.address}:{peer.port}")
        request_all_available(peer, piece_length, remaining_bytes)
        return 0 , 0
    
    elif (message_id == Message_Type.INTERESTED.value):
        peer.peer_interested = True
        print(f"Interested Message From {peer.address}:{peer.port}")
        unchoke_message = struct.pack(">IB",1,1) #Send unchoke Message
        peer.am_choking = False
        peer.socket.sendall(unchoke_message)
        return 0 , 0
    
    elif (message_id == Message_Type.NOT_INTERESTED.value):
        peer.peer_interested = False
        print(f"Uninterested Message From {peer.address}:{peer.port}")
        return 0 , 0
    
    elif (message_id == Message_Type.HAVE.value):
        
        index, = struct.unpack(">I", data[5:])
        try:
            peer.bitfield[index] = True
        except IndexError:
            print("Index Error From Have. Index ",index," is out of bounds")
        if peer.peer_choking == True and not peer.am_interested:
            interested_message = struct.pack(">IB",1,2) #Send interested Message
            peer.socket.sendall(interested_message)
            peer.am_interested = True
        pront(f"Have Message From {peer.address}:{peer.port}")
        return 0 , 0

    elif (message_id == Message_Type.BITFIELD.value):
        bitfield_length = payload_length - 1
        peer.bitfield.frombytes(data[5:bitfield_length + 5])
        if peer.peer_choking == True and not peer.am_interested:
            interested_message = struct.pack(">IB",1,2) #Send interested Message
            peer.socket.sendall(interested_message)
            peer.am_interested = True
        pront(f"Bitfield Message From {peer.address}:{peer.port}")
        return 0 , 0
    
    elif (message_id == Message_Type.REQUEST.value):
        pront(f"Request Message From {peer.address}:{peer.port}")
        return 0 , 0
    
    elif (message_id == Message_Type.PIECE.value):
        
        block_length = len(data) - 13
        payload_length, message_id, piece_index, block_offset, block = struct.unpack(">IBII{}s".format(block_length),
                                                                              data[:13 + block_length])
        pront(f"Piece {piece_index} received, begin at {block_offset}, size of block: {len(block)} From {peer.address}:{peer.port}.")
        '''
        block_hash = hashlib.sha1(block).digest()
        if block_hash != piece_hashes[piece_index]:
            print("piece index:",piece_index)
            print("HASHES DONT MATCH:", piece_hashes[piece_index], ":", block_hash)
            return -1, {'piece_index': piece_index}
            '''
        #path = save_piece_to_storage(block, piece_index, download_directory)
        return 1, {'piece_index': piece_index, 'block_begin': block_offset, 'block_length': len(block), 'block_data': block}
        #return 1, {'piece_index': piece_index, 'block_begin': block_offset, 'block_length': len(block), 'block_data': block}
        #return piece_index, block
    
    elif (message_id == Message_Type.CANCEL.value):
        pront(f"Cancel Message From {peer.address}:{peer.port}")
        # Used to cancel sending a piece in response to a request 
        # Not entirely sure how we're supposed to implement this
        return 0 , 0
    
    elif (message_id == Message_Type.PORT.value):
        pront(f"Port Message From {peer.address}:{peer.port}")
        return 0 , 0
    
    else:
        print("Invalid Message ID:",message_id)
        return 0 , 0
    
    
def read_from_socket(sock):
    data = b''

    while True:
        try:
            buff = sock.recv(4096)
            if len(buff) <= 0:
                break

            data += buff
        except socket.error as e:
            if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                print("Wrong errno {}".format(e.errno))
            break
        except Exception:
            print("Recv failed")
            break

    return data

def has_unchoked(peers):
    for peer in peers:
        if peer.peer_choking == False:
            return True
    return False

def get_random_peer_having_piece(peers, index,number_of_pieces):
    ready_peers = []
    for peer in peers:
        if peer.peer_choking == False and peer.am_interested == True and peer.bitfield.count() == number_of_pieces and peer.bitfield[index]:
            ready_peers.append(peer)
    return random.choice(ready_peers) if ready_peers else None


def pront(*text):
    if verbose:
        print(text)
