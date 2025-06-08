#!/usr/bin/python3
import threading
from socket import SHUT_RDWR
from bencode import bdecode, bencode
from urllib.parse import urlencode, urlparse
from enum import Enum
from main_bittorrent import Peer
import file_bittorrent
import bitarray
import os
import struct
# import glob
import os

peerlist = [] #Global list of peers
#this file will be run in a process
#Wait for requests to send data
#Get lock, read file
#Send data

file_directory = "downloaded_pieces"

my_id = b"-BT0001-123456789012"  # Our peer ID. Should this be stored somewhere else?


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


def handle_upload(peer, file_path):
    try:
        with open(file_path, 'rb') as file:
            while True:
                data = file.read(16384)  # Read 16KB of data from the file
                if not data:
                    break
                peer.socket.sendall(data)
        print(f"File {file_path} successfully sent to {peer.address}:{peer.port}")
    except Exception as e:
        print(f"Error uploading file to {peer.address}:{peer.port}: {e}")

total_pieces = 2512
# Function to handle connections from peers requesting files
def handle_connections(server_socket):
    while True:
        handle_request(peer)
        
        
def shutdown_peer_socket(peer: Peer, message):
    print(message)
    try:
        peer.socket.shutdown(socket.SHUT_RDWR)
        peer.socket.close()
    except Exception as e:
        print(f"Error shutting down connection: {e}")
    finally:
        if peer in peerlist:
            peerlist.remove(peer)

# Function to perform the handshake with a peer
def perform_handshake(peer: Peer):
    protocol_name = b"BitTorrent protocol"
    reserved_bytes = b"\x00" * 8
    # Receive the response from the peer
    response = peer.socket.recv(1 + 256 + 48)
    
    # Parse the response to check if the handshake was successful
    if len(response) != 68:
        print("Handshake failed: Invalid response length")
        return False

    peer_protocol_name_length = response[0]
    if peer_protocol_name_length != len(protocol_name):
        print("Handshake failed: Invalid protocol name length")
        return False

    peer_protocol_name = response[1:peer_protocol_name_length+1]
    if peer_protocol_name != protocol_name:
        print("Handshake failed: Invalid protocol name")
        return False
    
    peer_info_hash = response[peer_protocol_name_length:peer_protocol_name_length+20]
    print("Requested info hash: {peer_info_hash}")
    try:
        dir_list = os.listdir("{file_directory}/{peer_info_hash}")
    except:
        shutdown_peer_socket(peer, "Info Hash not found, closing connection")
        return False
    if len(dir_list) == 0:
        # If we have no pieces
        shutdown_peer_socket(peer, "Info Hash not found, closing connection")
        return False
    
    peer.info_hash = peer_info_hash
    peer.id = response[peer_protocol_name_length+20:] 
       
    
    # Construct the handshake message
    handshake_msg = (
        len(protocol_name).to_bytes(1, byteorder='big') +
        protocol_name +
        reserved_bytes +
        peer_info_hash +
        my_id
    )
    
    print(f"Sending handshake: {handshake_msg}")

    # Send the handshake message to the peer
    peer.socket.sendall(handshake_msg)
    peer.state = "HANDSHAKE SENT" # Or whatever state we 

    # Handshake successful
    print("Handshake successful, sending bitfield")
    #Sending bitfiel
    bitfield = create_bitfield("downloaded_pieces", total_pieces)
    bitfield_msg = struct.pack('>IB', total_pieces, 5) + bitfield
    peer.socket.sendall(bitfield_msg)
    return True

# Function to handle requests from a peer
def handle_message(peer: Peer):
    try:
        data = peer.socket.recv(4096) 

        if (data[0] == 0): # Keep alive message
            # Keep alive message
            # Reset peer ttl
            return 0
        
        if (data[4] == Message_Type.CHOKE):
            peer.am_choking = True
            return 0
        elif (data[4] == Message_Type.UNCHOKE):
            peer.am_choking = False
            return 0
        elif (data[4] == Message_Type.INTERESTED):
            peer.peer_interested = True
            # If peer is interested and we're choking them, send unchoke
            if peer.am_choking:
                send_unchoke(peer)
            return 0
        elif (data[4] == Message_Type.NOT_INTERESTED):
            peer.peer_interested = False
            return 0
        elif (data[4] == Message_Type.HAVE):
            return 0
        elif (data[4] == Message_Type.BITFIELD):
            return 0
        elif (data[4] == Message_Type.REQUEST):
            handle_request(peer, data)
            return 0
        elif (data[4] == Message_Type.PIECE):
            return 0
        elif (data[4] == Message_Type.CANCEL):
            # Used to cancel sending a piece in response to a request 
            return 0
        elif (data[4] == Message_Type.PORT):
            return 0
            
    except Exception as e:
        print(f"handle_message: Error handling request from {peer.address}:{peer.port}: {e}")
        

def handle_request(peer, data):
    try:
        index, begin, length = struct.unpack('!III', data[5:17])
        file_path = os.path.join(file_directory, f"piece_{index}.bin")

        with open(file_path, 'rb') as file:
            file.seek(begin)
            block = file.read(length)

        # Prepare the response message
        response = struct.pack('!IbII', 9 + len(block), 7, index, begin) + block
        peer.socket.sendall(response)
        print(f"Block sent to {peer.address}:{peer.port}, index: {index}, begin: {begin}, length: {len(block)}")
    except FileNotFoundError:
        print(f"File not found: {file_path}")
    except Exception as e:
        print(f"Error handling request from {peer.address}:{peer.port}: {e}")

def handle_peer_connection(peer):
    # This function will handle each peer connection
    # Perform handshake or any other initial communication
    if perform_handshake(peer):
        # Once handshake is successful, listen for messages from this peer
        try:
            while True:
                data = peer.socket.recv(4096)
                if not data:
                    break  # If no data, peer has closed connection
                handle_request(peer, data)
        except Exception as e:
            print(f"Error handling peer {peer.address}: {e}")
        finally:
            shutdown_peer_socket(peer, "Peer disconnected")
    else:
        shutdown_peer_socket(peer, "Handshake failed")
        
        
def optimistic_unchoke():
    if len(peerlist) > 0:
        random_peer = random.choice(peerlist)
        if random_peer.am_choking:
            random_peer.am_choking = False
            send_unchoke(random_peer)
            
def send_unchoke(peer):
    try:
        # Unchoke message structure: <length_prefix><message_id>
        # Length prefix is 1 (size of the message ID), and the message ID for 'unchoke' is 1.
        unchoke_message = struct.pack('!IB', 1, 1)  # '!I' for 4-byte unsigned int, '!B' for 1-byte unsigned char
        peer.socket.sendall(unchoke_message)
        peer.am_choking = False  # Update our status to reflect that we are no longer choking this peer
        print(f"Sent unchoke to {peer.address}:{peer.port}")
    except Exception as e:
        print(f"Failed to send unchoke to {peer.address}:{peer.port}: {e}")
        shutdown_peer_socket(peer, "Failed to send unchoke")
        
        
def create_bitfield(download_directory, total_pieces):
    bitfield = bitarray(total_pieces)
    bitfield.setall(False)

    for file_name in os.listdir(download_directory):
        if file_name.startswith('piece_') and file_name.endswith('.bin'):
            try:
                # Extract the index from the file name
                index = int(file_name.split('_')[1].split('.')[0]) 
                bitfield[index - 1] = True  # Adjust index to start from 0
            except ValueError:
                print(f"Ignoring invalid file name: {file_name}")

    return bitfield


def get_peer_of_socket(socket):
    for peer in peerlist:
        if peer.socket is socket:
            return peer
    
    return -1
