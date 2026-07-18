import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / 'scripts' / 'inputxml_canonical_compiler.py'
spec = importlib.util.spec_from_file_location('inputxml_canonical_compiler_seljson_test', MODULE_PATH)
compiler = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = compiler
spec.loader.exec_module(compiler)
CONTRACTS = ROOT / 'contracts' / 'inputxml' / 'v1'


def node(number, position):
    position_xml = f'<Position>{position}</Position>' if position else ''
    return f'''<Node><NodeNumber>{number}</NodeNumber><NodeName>N{number}</NodeName><Endpoint>1</Endpoint><Rigid>0</Rigid><ComponentType>PIPE</ComponentType><Weight>0</Weight><ComponentRefNo>SEL-{number}</ComponentRefNo><ConnectionType/><OutsideDiameter>114.3</OutsideDiameter><WallThickness>6.02</WallThickness><CorrosionAllowance>0</CorrosionAllowance><InsulationThickness>0</InsulationThickness>{position_xml}<BendRadius>0</BendRadius><SIF>0</SIF></Node>'''


def source_xml(second_position='1000 0 0'):
    return f'''<Root><Branch><Branchname>SEL-L-100</Branchname><LineNo>SEL-L-100</LineNo><Pressure><Pressure1>1000</Pressure1><HydroPressure>1500</HydroPressure></Pressure><Temperature><Temperature1>100</Temperature1></Temperature><MaterialNumber>106</MaterialNumber><InsulationDensity>0</InsulationDensity><FluidDensity>965</FluidDensity>{node('10','0 0 0')}{node('20',second_position)}</Branch></Root>'''.encode()


class SelectionJsonAdapterTests(unittest.TestCase):
    def compile(self, source):
        return compiler.compile_document(
            source,
            source_name='selection-json-custom-root.xml',
            source_repository='reallaksh19/3D_Converters',
            source_path='test://selection-json-custom-root.xml',
            producer='selection-json-adapter-test',
            registry_path=CONTRACTS / 'inputxml-dialect-registry.json',
            alias_registry_path=CONTRACTS / 'catalogs' / 'inputxml-alias-registry.json',
            canonical_xsd_path=CONTRACTS / 'inputxml-canonical-v1.xsd',
            categorized_xsd_path=CONTRACTS / 'categorized-inputxml-v3.xsd',
            semantic_rules_path=CONTRACTS / 'inputxml-semantic-rules-v1.sch',
            dialect_override='seljson-custom-root',
            context={'jobName':'SELJSON_CANONICAL_TEST'},
        )

    def test_explicit_selectionjson_dialect_passes(self):
        root, ledger, validation = self.compile(source_xml())
        self.assertIsNotNone(root)
        self.assertEqual('PASS', validation['status'])
        self.assertEqual('seljson-custom-root', ledger['dialect'])
        element = root.find('PIPINGMODEL/PIPINGELEMENT')
        self.assertEqual('SEL-L-100', element.get('LINE'))
        self.assertTrue(any(row['sourceType'] == 'SELECTIONJSON_CUSTOM_ROOT' for row in ledger['records']))
        records = root.xpath('//ext:Record', namespaces={'ext': compiler.EXT_NS})
        self.assertTrue(any(row.get('name') == 'SelectionJsonSourceDocument' for row in records))
        self.assertFalse(any(row.get('name') == 'XmlBuilderSourceDocument' for row in records))

    def test_selectionjson_topology_loss_blocks(self):
        root, ledger, validation = self.compile(source_xml(second_position=''))
        self.assertEqual('BLOCKED', validation['status'])
        self.assertTrue(any('NODE_DROPPED_MISSING_POSITION' in str(row['sourceFields']) for row in ledger['records']))


if __name__ == '__main__':
    unittest.main()
