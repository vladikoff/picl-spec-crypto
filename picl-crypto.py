# -*- coding: utf-8 -*-
# this should work with both python2.7 and python3.3

from hashlib import sha256
import hmac
from hkdf import HKDF
import itertools, binascii, time, sys
import six
from six import binary_type, print_, int2byte
import mysrp

# get scrypt-0.6.1 from PyPI, run this with it in your PYTHONPATH
# https://pypi.python.org/pypi/scrypt/0.6.1
import scrypt

# PyPI has four candidates for PBKDF2 functionality. We use "simple-pbkdf2"
# by Armin Ronacher: https://pypi.python.org/pypi/simple-pbkdf2/1.0 . Note
# that v1.0 has a bug which causes segfaults when num_iterations is greater
# than about 88k.
from pbkdf2 import pbkdf2_bin

# other options:
# * https://pypi.python.org/pypi/PBKDF/1.0
#   most mature, but hardwired to use SHA1
#
# * https://pypi.python.org/pypi/pbkdf2/1.3
#   doesn't work without pycrypto, since its hashlib fallback is buggy
#
# * https://pypi.python.org/pypi/pbkdf2.py/1.1
#   also looks good, but ships in multiple files

def HMAC(key, msg):
    return hmac.new(key, msg, sha256).digest()
def printheader(name):
    print_("== %s ==" % name)
    print_()

def printhex(name, value, groups_per_line=1):
    assert isinstance(value, binary_type), type(value)
    h = binascii.hexlify(value).decode("ascii")
    groups = [h[i:i+16] for i in range(0, len(h), 16)]
    lines = [" ".join(groups[i:i+groups_per_line])
             for i in range(0, len(groups), groups_per_line)]
    print_("%s:" % name)
    for line in lines:
        print_(line)
    print_()
def printdec(name, n):
    print_(name+" (base 10):")
    s = str(n)
    while len(s)%32:
        s = " "+s
    for i in range(0, len(s), 32):
        print_(s[i:i+32].replace(" ",""))
    print_()

def thencount(*values):
    for v in values:
        yield v
    for c in itertools.count():
        yield c

def split(value):
    assert len(value)%32 == 0
    return [value[i:i+32] for i in range(0, len(value), 32)]
def KW(name):
    return b"identity.mozilla.com/picl/v1/" + six.b(name)
def KWE(name, emailUTF8):
    return b"identity.mozilla.com/picl/v1/" + six.b(name) + b":" + emailUTF8

def xor(s1, s2):
    assert isinstance(s1, binary_type), type(s1)
    assert isinstance(s2, binary_type), type(s2)
    assert len(s1) == len(s2)
    return b"".join([int2byte(ord(s1[i:i+1])^ord(s2[i:i+1])) for i in range(len(s1))])

def fakeKey(start):
    return b"".join([int2byte(c) for c in range(start, start+32)])

printheader("stretch-KDF")
emailUTF8 = u"andré@example.org".encode("utf-8")
passwordUTF8 = u"pässwörd".encode("utf-8")
printhex("email", emailUTF8)
printhex("password", passwordUTF8)

# stretching
time_start = time.time()
k1 = pbkdf2_bin(passwordUTF8, KWE("first-PBKDF", emailUTF8),
                20*1000, keylen=1*32, hashfunc=sha256)
time_k1 = time.time()
printhex("K1", k1)
k2 = scrypt.hash(k1, KW("scrypt"), N=64*1024, r=8, p=1, buflen=1*32)
time_k2 = time.time()
printhex("K2", k2)
stretchedPW = pbkdf2_bin(k2+passwordUTF8, KWE("second-PBKDF", emailUTF8),
                         20*1000, keylen=1*32, hashfunc=sha256)
time_k3 = time.time()
#print "stretching took %0.3f seconds (P=%0.3f + S=%0.3f + P=%0.3f)" % \
#      (time_k3-time_start,
#       time_k1-time_start, time_k2-time_k1, time_k3-time_k2)

printhex("stretchedPW", stretchedPW)
mainSalt = fakeKey(0)

(srpPW, unwrapBKey) = split(HKDF(SKM=stretchedPW,
                                 XTS=mainSalt,
                                 CTXinfo=KW("mainKDF"),
                                 dkLen=2*32))

if 1:
    printheader("main-KDF")
    printhex("mainSalt (normally random)", mainSalt)
    printhex("srpPW", srpPW)
    printhex("unwrapBKey", unwrapBKey)

kA = fakeKey(1*32)
wrapkB = fakeKey(2*32)
signToken = fakeKey(3*32)
resetToken = fakeKey(4*32)

# choose a salt that gives us a verifier with a leading zero, to ensure we
# exercise padding behavior in implementations of this spec. Otherwise
# padding bugs (dropping a leading zero) would hide in about 255 out of 256
# test runs.
def findSalt():
    print_("looking for srpSalt that yields an srpVerifier with leading zero")
    makeV = mysrp.create_verifier
    prefix = b"\x00"+b"\xf1"+b"\x00"*14
    for count in thencount(112):
        # about 500 per second
        if count > 300 and count % 500 == 0:
            print_(count, "tries")
        if count > 1000000:
            raise ValueError("unable to find suitable salt in reasonable time")
        salt = prefix + binascii.unhexlify("%032x"%count)
        (srpVerifier, v_num, x_str, x_num, _) = makeV(emailUTF8, srpPW, salt)
        if srpVerifier[0:1] != b"\x00":
            continue
        print_("found salt on count", count)
        printdec("internal x", x_num)
        printhex("internal x (hex)", x_str)
        #print_(" v", binascii.hexlify(srpVerifier))
        printdec("v (verifier as number)", v_num)
        return salt, srpVerifier, v_num

srpSalt, srpVerifier, v_num = findSalt()

if 1:
    printheader("SRP Verifier")
    printdec("k", mysrp.k)
    printhex("srpSalt (normally random)", srpSalt)
    printhex("srpVerifier", srpVerifier, groups_per_line=2)

def findB():
    print_("looking for 'b' that yields srpA with leading zero")
    prefix = b"\x00"+b"\xf3"+b"\x00"*(256-2-16)
    s = mysrp.Server(srpVerifier)
    for count in thencount(5):
        if count > 300 and count % 500 == 0:
            print_(count, "tries")
        if count > 1000000:
            raise ValueError("unable to find suitable value in reasonable time")
        b_str = prefix + binascii.unhexlify("%032x"%count)
        assert len(b_str) == 2048/8, (len(b_str),2048/8)
        b = mysrp.bytes_to_long(b_str)
        B = s.one(b)
        if B[0:1] != b"\x00":
            continue
        print_("found b on count", count)
        printdec("private b (normally random)", b)
        printhex("private b (hex)", b_str, groups_per_line=2)
        return b,B

if 1:
    printheader("SRP B")
    b,B = findB()
    printhex("transmitted srpB", B, groups_per_line=2)
    assert mysrp.Server(srpVerifier).one(b) == B

def findA():
    print_("looking for 'a' that yields srpA with leading zero")
    # 'a' is in [1..N-1], so 2048 bits, or 256 bytes
    prefix = b"\x00"+b"\xf2"+b"\x00"*(256-2-16)
    c = mysrp.Client()
    import time
    start = time.time()
    num_near_misses = 0
    # hm.. this reports an awful lot of consecutive "near-misses". But, this
    # a->A transformation isn't supposed to be strong against related "keys".
    for count in thencount(2599):
        # this processes about 50 per second. 2^16 needs about 20 minutes.
        if count > 300 and count % 500 == 0:
            now = time.time()
            print_(count, "tries", now - start)
            start = now
        if count > 1000000:
            raise ValueError("unable to find suitable value in reasonable time")
        a_str = prefix + binascii.unhexlify("%032x"%count)
        assert len(a_str) == 2048/8, (len(a_str),2048/8)
        a = mysrp.bytes_to_long(a_str)
        A = c.one(a)
        if A[0:1] != b"\x00":
            continue
        num_near_misses += 1
        # also require that the computed S has a leading zero
        c.two(B, srpSalt, emailUTF8, srpPW)
        if c._debug_S_bytes[0:1] != b"\x00":
            print_("found good A, but not good S, on count %d (near misses=%d)"
                   % (count, num_near_misses))
            continue
        print_("found a on count", count)
        printdec("private a (normally random)", a)
        printhex("private a (hex)", a_str, groups_per_line=2)
        return a,A

if 1:
    printheader("SRP A")
    a,A = findA()
    printhex("transmitted srpA", A, groups_per_line=2)
    assert mysrp.Client().one(a) == A


if 1:
    printheader("SRP key-agreement")
    c = mysrp.Client()
    s = mysrp.Server(srpVerifier)
    Ax = c.one(a)
    assert A==Ax
    M1 = c.two(B, srpSalt, emailUTF8, srpPW)
    Bx = s.one(b)
    assert Bx==B
    s.two(A,M1)
    assert c.get_key() == s.get_key()
    printhex("u", c._debug_u_bytes, groups_per_line=2)
    printhex("S", c._debug_S_bytes, groups_per_line=2)
    printhex("M1", M1)
    srpK = c.get_key()
    printhex("srpK", srpK)

if 1:
    printheader("getSignToken request")
    #srpK = fakeKey(0)

    x = HKDF(SKM=srpK,
             dkLen=(1+3)*32,
             XTS=None,
             CTXinfo=KW("getSignToken"))
    respHMACkey = x[0:32]
    respXORkey = x[32:]
    printhex("srpK", srpK)
    printhex("respHMACkey", respHMACkey)
    printhex("respXORkey", respXORkey)

    plaintext = kA+wrapkB+signToken
    printhex("plaintext", plaintext)

    ciphertext = xor(plaintext, respXORkey)
    printhex("ciphertext", ciphertext)
    mac = HMAC(respHMACkey, ciphertext)
    printhex("MAC", mac)
    printhex("response", ciphertext+mac)

if 1:
    printheader("signCertificate")
    tokenID,reqHMACkey = split(HKDF(SKM=signToken,
                                    XTS=None,
                                    dkLen=2*32,
                                    CTXinfo=KW("signCertificate")))
    printhex("signToken", signToken)
    printhex("tokenID", tokenID)
    printhex("reqHMACkey", reqHMACkey)

if 1:
    printheader("resetAccount")
    newSRPv = fakeKey(5*32)+fakeKey(6*32)
    newSRPsalt = fakeKey(7*32)
    plaintext = wrapkB+newSRPv+newSRPsalt
    keys = HKDF(SKM=resetToken,
                XTS=None,
                dkLen=2*32+len(plaintext),
                CTXinfo=KW("resetAccount"))
    tokenID = keys[0:32]
    reqHMACkey = keys[32:64]
    reqXORkey = keys[64:]
    printhex("resetToken", resetToken)
    printhex("tokenID", tokenID)
    printhex("reqHMACkey", reqHMACkey)
    printhex("reqXORkey", reqXORkey)
    printhex("plaintext", plaintext)
    ciphertext = xor(plaintext, reqXORkey)
    printhex("ciphertext", ciphertext)

