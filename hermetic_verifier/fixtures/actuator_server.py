import pathlib, socket
s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); s.bind(("0.0.0.0",4711)); s.listen(1)
c,_=s.accept(); data=c.recv(4096); pathlib.Path("/receipts/actuator.txt").write_bytes(data); c.sendall(b"ACTUATED"); c.close(); s.close()
