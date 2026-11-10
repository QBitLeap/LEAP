from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from quantums.cli import command_inscribe, command_wallet_batch, parser
from quantums.names import next_name, used_names, require_available, pending_names


class NamePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.index = self.root / "index"; self.index.mkdir()
        with sqlite3.connect(self.index / "leap-index.sqlite3") as db:
            db.execute("CREATE TABLE index_config(singleton,network)")
            db.execute("INSERT INTO index_config VALUES(1,'testnet4')")
            db.execute("CREATE TABLE blocks(height,hash)")
            db.execute("INSERT INTO blocks VALUES(1,'tip')")
        with sqlite3.connect(self.index / "quantum.sqlite3") as db:
            db.execute("CREATE TABLE index_tip(singleton,height,hash)")
            db.execute("INSERT INTO index_tip VALUES(1,1,'tip')")
            db.execute("CREATE TABLE quantums(quantum_id,name)")
            db.execute("INSERT INTO quantums VALUES('existing:0','Sample Name')")
        self.file = self.root / "art.txt"; self.file.write_text("Art")

    def test_suffixes_are_exact_case_sensitive_and_skip_taken_numbers(self):
        self.assertEqual(next_name('Sample Name',set()),'Sample Name')
        self.assertEqual(next_name('Sample Name',{'Sample Name'}),'Sample Name 2')
        self.assertEqual(next_name('Sample Name',{'Sample Name','Sample Name 2'}),'Sample Name 3')
        self.assertEqual(next_name('sample name',{'Sample Name'}),'sample name')
        self.assertEqual(next_name('Edition 2026',{'Edition 2026'}),'Edition 2026 2')

    def test_network_tips_limits_and_literal_prefixes(self):
        self.assertEqual(used_names(self.index,'testnet4',['Sample Name']),{'Sample Name'})
        self.assertEqual(used_names(self.index,'testnet4',['Sample Name'],exclude_txids=['existing']),set())
        self.assertEqual(used_names(self.index,'testnet4',['%']),set())
        with self.assertRaisesRegex(ValueError,'wrong network'): used_names(self.index,'main',['Sample Name'])
        with self.assertRaisesRegex(ValueError,'too long'): next_name('x'*255,{'x'*255})
        for name in ['', ' ', 'x\n', '\x00', '😀'*64]:
            with self.assertRaises(ValueError): next_name(name,set())

    def test_duplicate_cli_name_is_rejected_before_wallet_preparation(self):
        args=parser().parse_args(['inscribe',str(self.file),'--name','Sample Name','--fee','1','--chain','testnet4','--index-data-dir',str(self.index)])
        with patch('quantums.cli.rpc_from_args',return_value=Mock(call=Mock(return_value=[]))), patch('quantums.cli.require_synchronized_index'), patch('quantums.cli.prepare_inscription') as sign:
            with self.assertRaisesRegex(ValueError,'Sample Name 2'): command_inscribe(args)
            sign.assert_not_called()

    def test_yaml_duplicates_and_chain_duplicates_are_rejected_before_signing(self):
        path=self.root/'batch.yaml'
        for names in [('Sample Name',),('New Art','New Art')]:
            path.write_text('mode: parentless\ninscriptions:\n'+''.join(f'  - file: art.txt\n    name: {name}\n' for name in names))
            args=parser().parse_args(['batch','--wallet','test','--batch',str(path),'--fee','1','--chain','testnet4','--index-data-dir',str(self.index)])
            with patch('quantums.cli.rpc_from_args',return_value=Mock(call=Mock(return_value=[]))), patch('quantums.cli.require_synchronized_index'), patch('quantums.cli.prepare_batch') as sign:
                with self.assertRaisesRegex(ValueError,'Next available name'): command_wallet_batch(args)
                sign.assert_not_called()
        require_available(self.index,'testnet4',['New Art','Sample Name 2'])

    def test_pending_canonical_quantum_names_block_reuse_and_ignore_own_txid(self):
        from quantums.envelope import create_artifact, encode
        from quantums.script import quantum_op_return
        artifact=create_artifact(b"art",name="Pending Art",content_type="text/plain")
        txid="ab"*32
        tx={"txid":txid,"vout":[{"value_bits":4000,"scriptPubKey":{"type":"witness_v2_p2mr"}},
            {"value_bits":0,"scriptPubKey":{"hex":quantum_op_return(encode(artifact)).hex()}}]}
        class Node:
            def call(self,method,*params):
                return [txid] if method=="getrawmempool" else tx
        self.assertEqual(pending_names(Node(),["Pending Art"],network="testnet4"),{"Pending Art"})
        self.assertEqual(pending_names(Node(),["Pending Art"],network="testnet4",exclude_txids=[txid]),set())
        with self.assertRaisesRegex(ValueError,'Pending Art 2'):
            require_available(self.index,'testnet4',["Pending Art"],rpc=Node())

    def test_mempool_changes_fail_closed(self):
        class Changing:
            count=0
            def call(self,method,*params):
                self.count+=1
                return [] if self.count==1 else ['new']
        with self.assertRaisesRegex(ValueError,'mempool changed'):
            pending_names(Changing(),['Art'],network='testnet4')

    def test_pending_catalog_only_fetches_new_transactions_and_prunes_dropped_names(self):
        from quantums.envelope import create_artifact, encode
        from quantums.script import quantum_op_return
        from quantums.names import PendingNameCatalog
        txid="cd"*32
        artifact=create_artifact(b"art",name="Pending Art",content_type="text/plain")
        tx={"txid":txid,"vout":[{"value_bits":4000,"scriptPubKey":{"type":"witness_v2_p2mr"}},
            {"value_bits":0,"scriptPubKey":{"hex":quantum_op_return(encode(artifact)).hex()}}]}
        class Node:
            pending=[txid]; fetched=0
            def call(self,method,*params):
                if method=="getrawmempool": return self.pending
                self.fetched+=1; return tx
        node=Node(); cache=self.root/'cache'
        for _ in range(3):
            self.assertEqual(pending_names(node,['Pending Art'],network='testnet4',cache_dir=cache),{'Pending Art'})
        self.assertEqual(node.fetched,1)
        self.assertEqual(PendingNameCatalog(cache).read('main'),{})
        node.pending=[]
        self.assertEqual(pending_names(node,['Pending Art'],network='testnet4',cache_dir=cache),set())
        self.assertEqual(PendingNameCatalog(cache).read('testnet4'),{})

    def test_name_taken_during_signing_blocks_cli_publication(self):
        from quantums.transaction import PreparedInscription
        args=parser().parse_args(['inscribe',str(self.file),'--name','New Art','--fee','1','--chain','testnet4','--index-data-dir',str(self.index)])
        class Node:
            sent=False
            def call(self,method,*params):
                if method=='sendrawtransaction': self.sent=True; raise AssertionError('Must not publish a taken name')
                if method=='getrawmempool': return []
                raise AssertionError(method)
        node=Node()
        def signed(*_args,**_kwargs):
            with sqlite3.connect(self.index/'quantum.sqlite3') as db:
                db.execute("INSERT INTO quantums VALUES('race:0','New Art')")
            return PreparedInscription('owner',4000,100,'00','0.00001',100,True,None,txid='ff'*32)
        with patch('quantums.cli.rpc_from_args',return_value=node), patch('quantums.cli.require_synchronized_index'), patch('quantums.cli.prepare_inscription',side_effect=signed):
            with self.assertRaisesRegex(ValueError,'New Art 2'): command_inscribe(args)
        self.assertFalse(node.sent)

    def test_accepted_local_publication_is_available_without_refetching(self):
        from quantums.names import PendingNameCatalog
        txid='ef'*32;cache=self.root/'cache'
        PendingNameCatalog(cache).remember('testnet4',txid,['Newly Published'])
        class Node:
            def call(self,method,*params):
                if method=='getrawmempool': return [txid]
                raise AssertionError('Known local transaction should not need a refetch')
        self.assertEqual(pending_names(Node(),['Newly Published'],network='testnet4',cache_dir=cache),{'Newly Published'})


if __name__=='__main__': unittest.main()
