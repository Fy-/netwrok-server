import os

import hashlib

import asyncio
import aiopg

import nwdb
import core
import mailqueue


@core.handler
def authenticate(client, email, password):
    """
    Authenticate the client by matching email and password.
    Note, the password must not be sent in cleartext, it is sent as a
    sha356(uid + sha256(password)), where uid is sent with the initial
    welcome message.
    """
    hash = client.uid
    with (yield from nwdb.connection()) as conn:
        cursor = yield from conn.cursor()
        yield from cursor.execute("""
        select A.id, A.handle, A.email, A.password, string_agg(D.name, ',') as roles
        from member A
        inner join member_role C on A.id = C.member_id
        inner join role D on D.id = C.role_id
        where lower(A.email) = lower(%s)
        group by 1,2,3,4
        """, [email])
        rs = yield from cursor.fetchone()
        authenticated = False
        roles = []
        if rs is None:
            authenticated = False
        else:
            h = (hash + rs[3]).encode("utf8")
            if hashlib.sha256(h).hexdigest() == password:
                client.member_id = client.session["member_id"] = rs[0]
                client.roles = roles[:] = rs[4].split(",")
                authenticated = True
                if 'Banned' in client.roles:
                    yield from client.send("auth.banned")
                    authenticated = False
            else:
                authenticated = False
        if(not authenticated):
            yield from asyncio.sleep(3)
        client.authenticated = authenticated
        yield from client.send("auth.authenticate", authenticated)
        if authenticated:
            yield from client.send("auth.info", dict(id=rs[0], handle=rs[1], roles=roles))


@core.handler
def register(client, handle, email, password):
    """
    Register a new user. Handle and email must be unique, and password
    must be sha256(password), not cleartext.
    """
    with (yield from nwdb.connection()) as conn:
        cursor = yield from conn.cursor()
        try:
            yield from cursor.execute("""
            insert into member(handle, email, password)
            select %s, %s, %s
            returning id
            """, [handle, email, password])
        except Exception as e:
            yield from client.send("auth.register", False)
        else:
            rs = yield from cursor.fetchone()
            client.session["member_id"] = rs[0]
            yield from mailqueue.send(client, email, "Welcome.", "Thanks for registering.")
            yield from client.send("auth.register", True)
 

@core.handler
def password_reset_request(client, email):
    """
    Request a password reset for an email address. A code is sent to the
    email address which must be passed in via th password_reset message.
    """
    with (yield from nwdb.connection()) as conn:
        cursor = yield from conn.cursor()
        token = hashlib.md5(os.urandom(8)).hexdigest()[:8]
        try:
            yield from cursor.execute("""
            insert into password_reset_request(member_id, token)
            select id, %s from member where lower(email) = lower(%s)
            returning id
            """, [token, email])
            rs = yield from cursor.fetchone()
        except Exception as e:
            yield from client.send("auth.password_reset_request", False)
        else:
            yield from mailqueue.send(client, email, "Password Reset Request", "Code: " + token)
            yield from client.send("auth.password_reset_request", True)
 

@core.function
def password_reset(client, email, token, password):
    """
    Change the password by using the provided token. The password must be
    sha256(password), not cleartext.
    """
    with (yield from nwdb.connection()) as conn:
        cursor = yield from conn.cursor()
        success = False
        try:
            yield from cursor.execute("""
            update member A 
            set password = %s
            where lower(A.email) = lower(%s)
            and exists (select token from password_reset_request where member_id = A.id and lower(token) = lower(%s))
            returning A.id 
            """, [password, email, token])
        except Exception as e:
            print(type(e), e)
            success = False
        else:
            rs = yield from cursor.fetchone()
            if rs is None:
                siccess = False
            else:
                success = True
                member_id = rs[0]
                yield from cursor.execute("delete from password_reset_request where member_id = %s", [member_id])
                yield from mailqueue.send(client, email, "Password Reset", "Success")

        return success
 

@core.handler
def ban(client, member_id):
    client.require_role('Operator')
    with (yield from nwdb.connection()) as conn:
        cursor = yield from conn.cursor()
        success = False
        yield from cursor.execute("""
        insert into member_role(member_id, role_id)
        select %s, id
        from role where name = 'Banned'
        """, member_id)


@core.handler
def unban(client, member_id):
    client.require_role('Operator')
    with (yield from nwdb.connection()) as conn:
        cursor = yield from conn.cursor()
        success = False
        yield from cursor.execute("""
        delete from member_role
        where member_id = %s and role_id = (select id from role where name = 'Banned')
        """, member_id)



