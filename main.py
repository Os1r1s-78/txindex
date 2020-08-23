import os
import cbor2
from binascii import unhexlify
from plyvel import DB, IteratorInvalidError
from bitcoin_requests import BitcoinRPC
from bitcoin.core.script import CScript, CScriptOp
from pprint import pprint as pp

BITCOIN_RPC_ADDRESS = os.getenv("BITCOIN_RPC_ADDRESS") or "http://127.0.0.1:8443"
BITCOIN_RPC_USER = os.getenv("BITCOIN_RPC_USER")
BITCOIN_RPC_PASSWORD = os.getenv("BITCOIN_RPC_PASSWORD")

bitcoin = BitcoinRPC(BITCOIN_RPC_ADDRESS, BITCOIN_RPC_USER, BITCOIN_RPC_PASSWORD)

next_block = bitcoin.getblockchaininfo()["blocks"]

db = DB("db", create_if_missing=True)


def main():
    load_txtypes()

    try:
        with db.raw_iterator() as ri:
            ri.seek_to_last()
            blockheight = int.from_bytes(ri.key(), "big")
    except IteratorInvalidError:
        blockheight = 0

    while blockheight < (next_block + 10):
        inspect_block(blockheight)
        blockheight += 1


def inspect_block(blockheight):
    block = bitcoin.getblock(bitcoin.getblockhash(blockheight), 2)
    blockdata = {}
    references = {blockheight: blockdata}

    # gather changes
    for tx in block["tx"]:
        for vout in tx["vout"]:
            # get data for transactions in this block
            template = script_to_template(vout["scriptPubKey"]["hex"])
            typn = get_or_set_txtype(tx["txid"], "script_pub_key: " + template)
            blockdata.setdefault(typn, 0)
            blockdata[typn] += 1
            if template == None or typn == None:
                pp(tx, "vout", vout, template, typn)
                exit()

        for vin in tx["vin"]:
            # get witness or p2sh data regarding previous txs in other blocks
            if (witness := vin.get("txinwitness")) and len(witness) <= 2:
                template = "p2wsh: " + script_to_template(witness[-1])
            elif (
                "scriptSig" in vin
                and (asm := vin["scriptSig"]["hex"])
                and asm[0:2] == "0 "
            ):
                # p2sh is stupid and encodes the redeem-script inside the scriptSig,
                # so we must decode it
                template = "p2sh: " + script_to_template(asm.split(" ")[-1])
            else:
                continue

            typn = get_or_set_txtype(tx["txid"], template)
            txid = vin["txid"]
            blockhash = bitcoin.getrawtransaction(txid, True)["blockhash"]
            h = bitcoin.getblock(blockhash, 1)["height"]
            hdata = references.get(h) or cbor2.loads(db.get(h.to_bytes(4, "big")))
            references[h] = hdata
            hdata.setdefault(typn, 0)
            hdata[typn] += 1

            if template == None or typn == None:
                pp(tx, "vin", vin, template, typn)
                exit()

    # write changes
    with db.write_batch() as b:
        for blockn, value in references.items():
            print(
                blockn,
                {
                    txtypes_rev[typn].replace("script_pub_key: ", ""): count
                    for typn, count in value.items()
                },
            )
            b.put(blockn.to_bytes(4, "big"), cbor2.dumps(value))


def script_to_template(hex_script):
    bin_script = unhexlify(hex_script)
    script = CScript(bin_script)
    items = []
    for entry in script:
        if type(entry) == CScriptOp:
            items.append(str(entry))
        elif type(entry) == bytes:
            items.append(f"<{len(entry)}>")
        else:
            items.append(str(entry))
    return " ".join(items)


def load_txtypes():
    global txtypes, txtypes_rev, examples
    try:
        with open("txtypes.cbor", "rb") as fp:
            txtypes = cbor2.load(fp)
    except FileNotFoundError:
        txtypes = {}
    txtypes_rev = {v: k for k, v in txtypes.items()}

    try:
        with open("examples.cbor", "rb") as fp:
            examples = cbor2.load(fp)
    except FileNotFoundError:
        examples = {}


def get_or_set_txtype(txid, template):
    global txtypes, txtypes_rev, examples

    if not (examples.get(template)):
        examples[template] = txid
        with open("examples.cbor", "wb") as fp:
            cbor2.dump(examples, fp)

    if typn := txtypes.get(template):
        return typn
    else:
        typn = len(txtypes)
        txtypes[template] = typn
        with open("txtypes.cbor", "wb") as fp:
            cbor2.dump(txtypes, fp)
        txtypes_rev = {v: k for k, v in txtypes.items()}
        return typn


try:
    main()
finally:
    db.close()
