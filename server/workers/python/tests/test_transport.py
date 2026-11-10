import json
import socket
import struct

import pytest

from sgz_agent import transport


def block(kind, body, endian='<'):
    body += b'\0' * (-len(body) % 4)
    size = len(body) + 12
    return struct.pack(endian+'II', kind, size) + body + struct.pack(endian+'I', size)


def packet(payload=b'private-session-token', seq=10, wifi=False):
    tcp = struct.pack('!HHIIBBHHH', 50000, 8443, seq, 0, 0x50, 0x18, 1024, 0, 0)
    ip = struct.pack('!BBHHHBBH4s4s', 0x45, 0, 40+len(payload), 1, 0, 64, 6, 0,
                     socket.inet_aton('192.0.2.1'), socket.inet_aton('192.0.2.2'))
    # PktMon may label raw 802.11 QoS data as linktype Ethernet.
    link = b'\x88\x02' + b'\0'*24 + b'\xaa\xaa\x03\0\0\0\x08\0' if wifi else b'\0'*12+b'\x08\0'
    return link + ip + tcp + payload


def capture(frames, endian='<'):
    header = block(0x0a0d0d0a, struct.pack(endian+'IHHq', 0x1a2b3c4d, 1, 0, -1), endian)
    header += block(1, struct.pack(endian+'HHI', 1, 0, 65535), endian)
    return header + b''.join(block(6, struct.pack(endian+'IIIII', 0, 0, 1_000_000+i,
                             len(frame), len(frame))+frame, endian) for i, frame in enumerate(frames))


@pytest.mark.parametrize('wifi,endian', [(False,'<'),(True,'<'),(False,'>')])
def test_capture_reports_game_flow_without_exposing_payload(tmp_path, wifi, endian):
    path=tmp_path/'input.pcapng'
    path.write_bytes(capture([packet(wifi=wifi)],endian))
    result=transport.summarize_capture(path, [50000])
    assert result['matched_packets']==1
    assert result['flows'][0]['payload_bytes']==21
    assert result['flows'][0]['remote']=='192.0.2.2:8443'
    assert result['api_scan_available'] is False
    assert 'private-session-token' not in json.dumps(result)


def test_repeated_packets_are_not_counted_as_new_response_data(tmp_path):
    path=tmp_path/'input.pcapng'
    path.write_bytes(capture([packet(),packet(),packet(seq=31,payload=b'next')]))
    result=transport.summarize_capture(path,[50000])
    assert result['duplicate_segments']==1
    assert result['flows'][0]['payload_bytes']==25


def test_same_sequence_with_different_bytes_is_reported_as_conflict(tmp_path):
    path=tmp_path/'input.pcapng'
    path.write_bytes(capture([packet(payload=b'abcdefgh'),packet(payload=b'ijklmnop')]))
    result=transport.summarize_capture(path,[50000])
    assert result['duplicate_segments']==0
    assert result['sequence_conflicts']==1
    assert result['flows'][0]['segments']==2


def test_short_capture_does_not_hide_later_complete_packet(tmp_path):
    full=packet(); short=full[:-4]
    data=capture([])+block(6,struct.pack('<IIIII',0,0,1_000_000,len(short),len(full))+short)
    data+=block(6,struct.pack('<IIIII',0,0,1_000_001,len(full),len(full))+full)
    path=tmp_path/'input.pcapng';path.write_bytes(data)
    result=transport.summarize_capture(path,[50000])
    assert result['truncated_packets']==1
    assert result['duplicate_segments']==0
    assert result['sequence_conflicts']==0
    assert result['flows'][0]['captured_payload_bytes']==38


def test_other_local_ports_do_not_leak_into_game_summary(tmp_path):
    path=tmp_path/'input.pcapng';path.write_bytes(capture([packet()]))
    result=transport.summarize_capture(path,[50001])
    assert result['matched_packets']==0 and result['flows']==[]


@pytest.mark.parametrize('damage', ['truncated','wrong_length'])
def test_broken_capture_is_reported_as_invalid_not_empty(tmp_path,damage):
    data=capture([packet()])
    data=data[:-1] if damage=='truncated' else data[:-4]+struct.pack('<I',12)
    path=tmp_path/'bad.pcapng';path.write_bytes(data)
    with pytest.raises(ValueError,match='invalid_pcapng'):
        transport.summarize_capture(path,[50000])


def test_offline_cli_does_not_initialize_game_or_require_config(monkeypatch,tmp_path,capsys):
    from sgz_agent import cli
    path=tmp_path/'input.pcapng';path.write_bytes(capture([packet()]))
    monkeypatch.setattr('sys.argv',['sgz-agent','inspect-transport','--input',str(path),'--local-port','50000',
                                   '--config',str(tmp_path/'does-not-exist.json')])
    cli.main()
    assert json.loads(capsys.readouterr().out)['matched_packets']==1
