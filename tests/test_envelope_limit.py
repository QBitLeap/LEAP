"""Exact envelope boundaries, shared across all native platform packages."""
import unittest
from unittest.mock import patch
from quantums.envelope import MAX_ENVELOPE_BYTES, EnvelopeError, create_artifact, encode, decode, encode_batch, decode_batch, split_batch
from quantums.script import quantum_op_return, extract_single_push

class EnvelopeLimitTests(unittest.TestCase):
 def test_exact_limit_both_versions_and_one_extra_byte(self):
  self.assertEqual(MAX_ENVELOPE_BYTES,198_000)
  for version in (1,2):
   with self.subTest(version=version):
    encoder=encode if version==1 else lambda a:encode_batch((a,))
    decoder=decode if version==1 else lambda b:decode_batch(b)[0]
    def item(size):
     return create_artifact(b'x'*size,content_type='application/octet-stream',name='boundary.bin',format_version=version,**({'metadata':{'title':'Boundary','traits':['one','two']}} if version==2 else {}))
    low,high=1,198_000
    while low<high:
     mid=(low+high+1)//2
     try:encoder(item(mid));low=mid
     except EnvelopeError:high=mid-1
    good=item(low);raw=encoder(good)
    self.assertEqual(len(raw),198_000);self.assertEqual(decoder(raw),good)
    script=quantum_op_return(raw);self.assertEqual(len(script),198_006)
    self.assertEqual(200_000-len(script),1_994);self.assertEqual(extract_single_push(script),raw)
    with self.assertRaises(EnvelopeError):encoder(item(low+1))
    with patch('quantums.envelope.MAX_ENVELOPE_BYTES',198_001):oversized=encoder(item(low+1))
    self.assertEqual(len(oversized),198_001)
    with self.assertRaises(EnvelopeError):decoder(oversized)
 def test_batch_uses_new_range_without_splitting_complete_items(self):
  items=tuple(create_artifact(b'x'*98_000,content_type='text/plain',name=f'{i}.txt',format_version=2) for i in range(3))
  chunks=split_batch(items,lambda start,stop:tuple(range(stop-start)))
  self.assertEqual(tuple(map(len,chunks)),(2,1))
  self.assertGreater(len(encode_batch(chunks[0])),190_000)
  self.assertLessEqual(len(encode_batch(chunks[0])),198_000)
  self.assertEqual([a.name for chunk in chunks for a in chunk],[a.name for a in items])

if __name__=='__main__':unittest.main()
