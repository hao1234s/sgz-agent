"""Protocol readiness and offline transport diagnostics; no game requests."""
from collections import Counter
from pathlib import Path
from hashlib import sha256
import socket
import struct


def protocol_status(config=None):
    from .client_api import readiness
    reason = readiness(config) if config is not None else 'api_identity_binding_required'
    return {
        'api_scan_available': reason is None,
        'reason': reason,
        'adapter': 'logged_in_client_lua_readonly_v1',
        'ui_fallback_automatic': False,
        'session_checked_at_scan': True,
        'resources': 'client_map_config',
        'bandits': 'personal_server_list',
        'world_bandits_complete': False,
        'occupancy': 'unknown',
        'max_points': 2500,
        'coordinate_range': [1, 1620],
    }


def _packets(data):
    """Read EPBs with section-local interfaces and timestamp options."""
    position, endian, interfaces = 0, None, []
    while position < len(data):
        if len(data)-position < 12:
            raise ValueError('invalid_pcapng:truncated_block')
        if data[position:position+4] == b'\x0a\x0d\x0d\x0a':
            magic = data[position+8:position+12]
            if magic not in (b'\x4d\x3c\x2b\x1a', b'\x1a\x2b\x3c\x4d'):
                raise ValueError('invalid_pcapng:byte_order')
            endian = '<' if magic[0] == 0x4d else '>'
            interfaces = []
        if endian is None:
            raise ValueError('invalid_pcapng:missing_section')
        kind, size = struct.unpack_from(endian+'II', data, position)
        if size < 12 or size % 4 or position+size > len(data):
            raise ValueError('invalid_pcapng:block_length')
        block = data[position:position+size]
        if struct.unpack_from(endian+'I', block, size-4)[0] != size:
            raise ValueError('invalid_pcapng:block_trailer')
        position += size
        if kind == 0x0a0d0d0a and size < 28:
            raise ValueError('invalid_pcapng:section_length')
        if kind == 1:
            if size < 20:
                raise ValueError('invalid_pcapng:interface_length')
            link = struct.unpack_from(endian+'H', block, 8)[0]
            resolution, offset, option = 1e-6, 0, 16
            while option < size-4:
                if option+4 > size-4:
                    raise ValueError('invalid_pcapng:option')
                code, length = struct.unpack_from(endian+'HH', block, option)
                if option+4+length > size-4:
                    raise ValueError('invalid_pcapng:option_length')
                value = block[option+4:option+4+length]
                option += 4 + (length+3)//4*4
                if code == 0:
                    break
                if code == 9 and length == 1:
                    resolution = 2**-(value[0]&127) if value[0]&128 else 10**-value[0]
                if code == 14 and length == 8:
                    offset = struct.unpack(endian+'q', value)[0]
            interfaces.append((link, resolution, offset))
        elif kind == 6:
            if size < 32:
                raise ValueError('invalid_pcapng:packet_length')
            interface, hi, lo, captured, wire = struct.unpack_from(endian+'IIIII', block, 8)
            if interface >= len(interfaces) or captured > wire or 28+(captured+3)//4*4 > size-4:
                raise ValueError('invalid_pcapng:packet_bounds')
            link, resolution, offset = interfaces[interface]
            yield link, ((hi<<32)|lo)*resolution+offset, block[28:28+captured], captured < wire
    if endian is None:
        raise ValueError('invalid_pcapng:empty_file')


def _tcp_packet(frame, link):
    ip = None
    if link == 1 and len(frame) >= 14:
        ether = int.from_bytes(frame[12:14], 'big')
        header = 14
        while ether in (0x8100, 0x88a8) and len(frame) >= header+4:
            ether = int.from_bytes(frame[header+2:header+4], 'big')
            header += 4
        if ether == 0x0800:
            ip = header
    # Observed PktMon export: raw Wi-Fi data labeled Ethernet. Check the
    # complete 802.11 header and LLC signature, never search arbitrary payload.
    if ip is None and link in (1, 105) and len(frame) >= 24:
        control = int.from_bytes(frame[:2], 'little')
        if control & 0x000f == 8 and not control & 0x4000:
            header = 24 + (6 if control & 0x0300 == 0x0300 else 0)
            if control & 0x0080:
                header += 2 + (4 if control & 0x8000 else 0)
            if frame[header:header+8] == b'\xaa\xaa\x03\0\0\0\x08\0':
                ip = header+8
    if link == 101:  # raw IP
        ip = 0
    if ip is None or len(frame) < ip+20 or frame[ip] >> 4 != 4 or frame[ip+9] != 6:
        return None
    ihl = (frame[ip] & 15)*4
    total = int.from_bytes(frame[ip+2:ip+4], 'big')
    # Fragmented IPv4 is not reconstructed by this metadata inspector.
    if ihl < 20 or total < ihl+20 or int.from_bytes(frame[ip+6:ip+8], 'big') & 0x3fff:
        return None
    tcp = ip+ihl
    if len(frame) < tcp+20:
        return None
    header = (frame[tcp+12] >> 4)*4
    if header < 20 or total < ihl+header or len(frame) < tcp+header:
        return None
    src, dst = socket.inet_ntoa(frame[ip+12:ip+16]), socket.inet_ntoa(frame[ip+16:ip+20])
    sport, dport, seq = struct.unpack_from('!HHI', frame, tcp)
    payload = frame[tcp+header:ip+total]
    return src, sport, dst, dport, seq, total-ihl-header, payload


def summarize_capture(path, local_ports):
    """Return scoped transport metadata only. Prefix hints are not decoders.

    Exact duplicate segments are removed; partially overlapping retransmits
    are not reassembled. This is diagnostic metadata, never map observations.
    """
    ports = set(local_ports)
    if not ports or any(type(p) is not int or not 1 <= p <= 65535 for p in ports):
        raise ValueError('invalid_local_ports')
    path = Path(path)
    if path.stat().st_size > 64*1024*1024:
        raise ValueError('capture_too_large:64MiB')
    flows, seen, complete_segments = {}, set(), {}
    packets = matched = duplicates = unsupported = truncated = conflicts = 0
    for link, stamp, frame, short in _packets(path.read_bytes()):
        packets += 1
        truncated += int(short)
        parsed = _tcp_packet(frame, link)
        if parsed is None:
            unsupported += 1
            continue
        src, sport, dst, dport, seq, length, payload = parsed
        if sport not in ports and dport not in ports:
            continue
        matched += 1
        outbound = sport in ports
        local = f'{src}:{sport}' if outbound else f'{dst}:{dport}'
        remote = f'{dst}:{dport}' if outbound else f'{src}:{sport}'
        direction = 'out' if outbound else 'in'
        key = (local, remote, direction)
        item = flows.setdefault(key, {'local': local, 'remote': remote, 'direction': direction,
            'payload_bytes': 0, 'captured_payload_bytes': 0, 'segments': 0,
            'first_packet_at': stamp, 'last_packet_at': stamp, 'prefix_hints': Counter()})
        item['last_packet_at'] = max(stamp, item['last_packet_at'])
        if not length:
            continue
        sequence = (key, seq, length)
        digest = sha256(payload).digest()
        signature = (sequence, len(payload), digest)
        if signature in seen:
            duplicates += 1
            continue
        seen.add(signature)
        if len(payload) == length:
            previous = complete_segments.get(sequence)
            if previous is not None and previous != digest:
                conflicts += 1
            complete_segments[sequence] = digest
        item['payload_bytes'] += length
        item['captured_payload_bytes'] += len(payload)
        item['segments'] += 1
        hint = 'unknown_binary_or_fragment'
        if len(payload) >= 5 and payload[0] in (20,21,22,23) and payload[1] == 3 and payload[2] <= 4:
            hint = 'tls_record_prefix'
        elif payload.startswith((b'GET ',b'POST ',b'HTTP/',b'HEAD ',b'PUT ')):
            hint = 'http_prefix'
        elif payload.lstrip().startswith((b'{',b'[')):
            hint = 'json_like_prefix'
        item['prefix_hints'][hint] += 1
    return {'api_scan_available': False, 'source': 'offline_capture_metadata',
        'packets': packets, 'matched_packets': matched, 'duplicate_segments': duplicates,
        'unsupported_packets': unsupported, 'truncated_packets': truncated, 'sequence_conflicts': conflicts,
        'flows': list(flows.values()), 'payload_included': False,
        'caution': 'No message decoder or API validation; TCP stream reassembly is not performed.'}
