import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / 'scripts' / 'inputxml_canonical_compiler.py'
spec = importlib.util.spec_from_file_location('inputxml_canonical_compiler_xml_builder_test', MODULE_PATH)
compiler = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = compiler
spec.loader.exec_module(compiler)
CONTRACTS = ROOT / 'contracts' / 'inputxml' / 'v1'


def node(number, position, *, component='PIPE', restraints=''):
    return f'''<Node><NodeNumber>{number}</NodeNumber><NodeName>N{number}</NodeName><Endpoint>1</Endpoint><Rigid>0</Rigid><ComponentType>{component}</ComponentType><Weight>0</Weight><ComponentRefNo>C-{number}</ComponentRefNo><ConnectionType/><OutsideDiameter>114.3</OutsideDiameter><WallThickness>6.02</WallThickness><CorrosionAllowance>0</CorrosionAllowance><InsulationThickness>0</InsulationThickness><Position>{position}</Position><BendRadius>0</BendRadius><SIF>0</SIF>{restraints}</Node>'''


def source_xml(*, second_position='1000 0 0', restraints=''):
    return f'''<Root><Branch><Branchname>L-100</Branchname><LineNo>L-100</LineNo><Pressure><Pressure1>1000</Pressure1><HydroPressure>1500</HydroPressure></Pressure><Temperature><Temperature1>100</Temperature1></Temperature><MaterialNumber>106</MaterialNumber><InsulationDensity>0</InsulationDensity><FluidDensity>965</FluidDensity>{node('10','0 0 0')}{node('20',second_position,restraints=restraints)}</Branch></Root>'''.encode()


class XmlBuilderAdapterTests(unittest.TestCase):
    def compile(self, source):
        return compiler.compile_document(
            source,
            source_name='xml-builder.xml',
            source_repository='reallaksh19/3D_Converters',
            source_path='test://xml-builder.xml',
            producer='xml-builder-adapter-test',
            registry_path=CONTRACTS / 'inputxml-dialect-registry.json',
            alias_registry_path=CONTRACTS / 'catalogs' / 'inputxml-alias-registry.json',
            canonical_xsd_path=CONTRACTS / 'inputxml-canonical-v1.xsd',
            categorized_xsd_path=CONTRACTS / 'categorized-inputxml-v3.xsd',
            semantic_rules_path=CONTRACTS / 'inputxml-semantic-rules-v1.sch',
            context={'jobName':'XML_BUILDER_TEST'},
        )

    def test_explicit_two_node_route_passes(self):
        root, ledger, validation = self.compile(source_xml())
        self.assertIsNotNone(root)
        self.assertEqual('PASS', validation['status'])
        element = root.find('PIPINGMODEL/PIPINGELEMENT')
        self.assertEqual('L-100', element.get('LINE'))
        self.assertEqual('1000', element.get('DELTA_X'))
        self.assertEqual('0', element.get('DELTA_Y'))
        self.assertEqual('0', element.get('DELTA_Z'))
        self.assertEqual('106', element.get('MATERIAL_NUM'))
        self.assertTrue(any(row['sourceType'] == 'ROOT_BRANCH_NODE' for row in ledger['records']))
        self.assertTrue(any(row['disposition'] == 'FILTER_COMPATIBILITY_DEFAULT' for row in ledger['records']))
        records = root.xpath('//ext:Record', namespaces={'ext': compiler.EXT_NS})
        self.assertTrue(any(row.get('name') == 'XmlBuilderSourceDocument' for row in records))

    def test_missing_position_blocks_topology_loss(self):
        root, ledger, validation = self.compile(source_xml(second_position=''))
        self.assertEqual('BLOCKED', validation['status'])
        self.assertTrue(any('NODE_DROPPED_MISSING_POSITION' in str(row['sourceFields']) for row in ledger['records']))

    def test_restraint_overflow_blocks(self):
        restraints = ''.join('<Restraint><Type>+Y</Type><Stiffness>1000</Stiffness><Gap>0</Gap><Friction>0.3</Friction></Restraint>' for _ in range(7))
        root, ledger, validation = self.compile(source_xml(restraints=restraints))
        self.assertEqual('BLOCKED', validation['status'])
        self.assertTrue(any('RESTRAINT_SLOT_TRUNCATED' in str(row['sourceFields']) for row in ledger['records']))


if __name__ == '__main__':
    unittest.main()
