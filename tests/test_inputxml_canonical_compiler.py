import importlib.util
import json
import tempfile
import unittest
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'inputxml_canonical_compiler.py'
spec = importlib.util.spec_from_file_location('compiler', MODULE_PATH)
compiler = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = compiler
spec.loader.exec_module(compiler)
compiler._compiler._validate_xsd = lambda root, path: []
compiler._compiler._validate_schematron = lambda root, path: []

_DIALECT_IDS = [
    'canonical-inputxml-v1','caesar-inputxml-pressure1','caesar-inputxml-pressure-c',
    'enriched-inputxml-app','inputxml-fragment','categorized-inputxml-v3','cii14-inputxml',
    'managed-stage-json','xml-builder-root-branch-node','pdf-input-echo','uxml-rvm-intake','nodeset-v1'
]
REGISTRY = {
    'schema':'InputXmlDialectRegistry.v1',
    'dialects':[
        {
            'dialectId': dialect_id,
            **({'canonicalAcceptance':'EXCLUDED'} if dialect_id == 'nodeset-v1' else {}),
        }
        for dialect_id in _DIALECT_IDS
    ]
}
ALIASES = {
    'aliases':[
        {'parentElement':'PIPINGELEMENT','alias':'LINE_ID','canonicalName':'LINE','aliasKind':'FIELD_NAME'},
        {'parentElement':'RESTRAINT','alias':'MU','canonicalName':'FRIC_COEF','aliasKind':'FIELD_NAME'},
    ],
    'patternAliases':[
        {'parentElement':'PIPINGELEMENT','aliasPattern':'PRESSURE{n}','canonicalPattern':'PRESSURE_C{n}','indexes':list(range(1,10)),'aliasKind':'FIELD_NAME'},
    ]
}

BASE_HEAD = '<CAESARII xmlns="COADE" VERSION="11.00" XML_TYPE="Input"><PIPINGMODEL xmlns="" JOBNAME="T" NUMELT="{numelt}" NUMNOZ="0" NOHGRS="0" NUMBEND="0" NUMRIGID="0" NUMEXPJNT="0" NUMREST="0" NUMFORCMNT="0" NUMUNFLOAD="0" NUMWIND="0" NUMELEOFF="0" NUMALLOW="0" NUMISECT="0" NORTH_X="0" NORTH_Y="1" NORTH_Z="0">'
BASE_TAIL = '</PIPINGMODEL></CAESARII>'

def element(attrs='', children=''):
    return f'<PIPINGELEMENT {attrs}>{children}</PIPINGELEMENT>'

class CompilerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.registry = root/'registry.json'; self.registry.write_text(json.dumps(REGISTRY))
        self.aliases = root/'aliases.json'; self.aliases.write_text(json.dumps(ALIASES))
        self.dummy = root/'dummy'

    def tearDown(self): self.tmp.cleanup()

    def compile(self, xml, dialect=None, context=None):
        return compiler.compile_document(
            xml.encode(), source_name='x.xml', source_repository='r', source_path='x.xml',
            producer='test', registry_path=self.registry, alias_registry_path=self.aliases,
            canonical_xsd_path=self.dummy, categorized_xsd_path=self.dummy,
            semantic_rules_path=self.dummy, dialect_override=dialect, context=context or {})

    def test_pressure_alias_sentinel_and_line_inheritance(self):
        e1 = element('FROM_NODE="10" TO_NODE="20" DELTA_X="-1.0101" DELTA_Y="100" DELTA_Z="-1.0101" DIAMETER="100" WALL_THICK="5" LINE_ID="L1" PRESSURE1="10"')
        e2 = element('FROM_NODE="20" TO_NODE="30" DELTA_X="50" DELTA_Y="0" DELTA_Z="0" DIAMETER="-1.0101" WALL_THICK="-1.0101" LINE_ID="L1" PRESSURE1="-1.0101"')
        root, ledger, validation = self.compile(BASE_HEAD.format(numelt=2)+e1+e2+BASE_TAIL)
        self.assertEqual('PASS', validation['status'])
        out = root.find('PIPINGMODEL').findall('PIPINGELEMENT')
        self.assertEqual('0', out[0].get('DELTA_X'))
        self.assertEqual('10', out[0].get('PRESSURE_C1'))
        self.assertEqual('100', out[1].get('DIAMETER'))
        self.assertEqual('10', out[1].get('PRESSURE_C1'))
        self.assertTrue(any(r['sourceType']=='INHERITED_FIELD' for r in ledger['records']))

    def test_alias_conflict_blocks(self):
        e = element('FROM_NODE="10" TO_NODE="20" DELTA_X="1" DELTA_Y="0" DELTA_Z="0" DIAMETER="100" WALL_THICK="5" LINE="L1" PRESSURE_C1="10" PRESSURE1="11"')
        root, ledger, validation = self.compile(BASE_HEAD.format(numelt=1)+e+BASE_TAIL)
        self.assertIsNone(root); self.assertEqual('BLOCKED', validation['status'])

    def test_cross_line_inheritance_blocks(self):
        e1 = element('FROM_NODE="10" TO_NODE="20" DELTA_X="1" DELTA_Y="0" DELTA_Z="0" DIAMETER="100" WALL_THICK="5" LINE="L1"')
        e2 = element('FROM_NODE="20" TO_NODE="30" DELTA_X="1" DELTA_Y="0" DELTA_Z="0" DIAMETER="-1.0101" WALL_THICK="-1.0101" LINE="L2"')
        root, _, validation = self.compile(BASE_HEAD.format(numelt=2)+e1+e2+BASE_TAIL)
        self.assertIsNone(root); self.assertEqual('BLOCKED', validation['status'])

    def test_source_count_mismatch_blocks(self):
        e = element('FROM_NODE="10" TO_NODE="20" DELTA_X="1" DELTA_Y="0" DELTA_Z="0" DIAMETER="100" WALL_THICK="5" LINE="L1"')
        root, _, validation = self.compile(BASE_HEAD.format(numelt=2)+e+BASE_TAIL)
        self.assertIsNone(root); self.assertEqual('BLOCKED', validation['status'])

    def test_unsupported_child_preserved_blocking(self):
        e = element('FROM_NODE="10" TO_NODE="20" DELTA_X="1" DELTA_Y="0" DELTA_Z="0" DIAMETER="100" WALL_THICK="5" LINE="L1"', '<ALLOWABLESTRESS X="1"/>')
        xml = BASE_HEAD.format(numelt=1).replace('NUMALLOW="0"','NUMALLOW="1"')+e+BASE_TAIL
        root, ledger, validation = self.compile(xml)
        self.assertIsNotNone(root); self.assertEqual('BLOCKED', validation['status'])
        self.assertTrue(any(r['disposition']=='UNSUPPORTED_BLOCKING' for r in ledger['records']))

    def test_restraint_overflow_blocks(self):
        children=''.join(f'<RESTRAINT NUM="{i}" NODE="10" TYPE="1" STIFFNESS="1" GAP="0" FRIC_COEF="0" CNODE="0" XCOSINE="1" YCOSINE="0" ZCOSINE="0"/>' for i in range(1,8))
        e=element('FROM_NODE="10" TO_NODE="20" DELTA_X="1" DELTA_Y="0" DELTA_Z="0" DIAMETER="100" WALL_THICK="5" LINE="L1"',children)
        xml=BASE_HEAD.format(numelt=1).replace('NUMREST="0"','NUMREST="1"')+e+BASE_TAIL
        root,_,validation=self.compile(xml)
        self.assertIsNone(root); self.assertEqual('BLOCKED',validation['status'])

    def test_fragment_requires_and_uses_context(self):
        frag='<PIPINGELEMENT FROM_NODE="10" TO_NODE="20" DELTA_X="1" DELTA_Y="0" DELTA_Z="0" DIAMETER="100" WALL_THICK="5" LINE="L1"/>'
        root,_,validation=self.compile(frag,context={'version':'11.00','jobName':'F','north':[0,1,0]})
        self.assertEqual('PASS',validation['status']); self.assertEqual('1',root.find('PIPINGMODEL').get('NUMELT'))

    def test_categorized_reconciliation(self):
        xml='''<CategorizedInputXML schema="categorized-inputxml/v3" sourceName="x.xml"><Line id="L1"><LineBlock><Branchname>L1</Branchname><LineNo>L1</LineNo></LineBlock><Element fromNode="10" toNode="20" elementKey="10-20"><RouteGeometryBlock><DELTA_X>1</DELTA_X><DELTA_Y>0</DELTA_Y><DELTA_Z>0</DELTA_Z></RouteGeometryBlock><SectionDimensionBlock><DIAMETER>100</DIAMETER><WALL_THICK>5</WALL_THICK></SectionDimensionBlock><OriginalElement>&lt;PIPINGELEMENT FROM_NODE="10" TO_NODE="20"/&gt;</OriginalElement></Element></Line></CategorizedInputXML>'''
        root,_,validation=self.compile(xml,context={'version':'11.00','north':[0,1,0]})
        self.assertEqual('PASS',validation['status']); self.assertEqual('L1',root.find('PIPINGMODEL/PIPINGELEMENT').get('LINE'))

    def test_categorized_conflict_blocks(self):
        xml='''<CategorizedInputXML schema="categorized-inputxml/v3" sourceName="x.xml"><Line id="L1"><LineBlock><Branchname>L1</Branchname><LineNo>L1</LineNo></LineBlock><Element fromNode="10" toNode="20" elementKey="10-20"><RouteGeometryBlock><DELTA_X>2</DELTA_X><DELTA_Y>0</DELTA_Y><DELTA_Z>0</DELTA_Z></RouteGeometryBlock><SectionDimensionBlock><DIAMETER>100</DIAMETER><WALL_THICK>5</WALL_THICK></SectionDimensionBlock><OriginalElement>&lt;PIPINGELEMENT FROM_NODE="10" TO_NODE="20" DELTA_X="1"/&gt;</OriginalElement></Element></Line></CategorizedInputXML>'''
        root,_,validation=self.compile(xml,context={'version':'11.00','north':[0,1,0]})
        self.assertIsNone(root); self.assertEqual('BLOCKED',validation['status'])

    def test_excluded_nodeset_dialect_is_rejected(self):
        root,ledger,validation=self.compile('<NodeSet/>', dialect='nodeset-v1')
        self.assertIsNone(root); self.assertEqual('BLOCKED',validation['status'])
        self.assertEqual('REJECT_INVALID',ledger['records'][0]['disposition'])

    def test_wrong_xml_type_blocks_before_normalization(self):
        xml=(BASE_HEAD.format(numelt=0)+BASE_TAIL).replace('XML_TYPE="Input"','XML_TYPE="Output"')
        with self.assertRaises(compiler.CompileBlocked):
            self.compile(xml)

if __name__=='__main__': unittest.main()
