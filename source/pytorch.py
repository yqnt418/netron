''' PyTorch backend '''

import json
import os

class ModelFactory: # pylint: disable=too-few-public-methods
    ''' PyTorch backend model factory '''
    def open(self, model): # pylint: disable=missing-function-docstring
        metadata = {}
        metadata_files = [
            ('pytorch-metadata.json', ''),
            ('onnx-metadata.json', 'onnx::')
        ]
        path = os.path.dirname(__file__)
        for entry in metadata_files:
            file = os.path.join(path, entry[0])
            with open(file, 'r', encoding='utf-8') as handle:
                for item in json.load(handle):
                    name = entry[1] + item['name']
                    metadata[name] = item
        metadata = Metadata(metadata)
        return _Model(metadata, model)

class _Model: # pylint: disable=too-few-public-methods
    def __init__(self, metadata, model):
        self.graph = _Graph(metadata, model)

    def to_json(self):
        ''' Serialize model to JSON message '''
        json_model = {
            'signature': 'netron:pytorch',
            'format': 'TorchScript',
            'graphs': [ self.graph.to_json() ]
        }
        return json_model

class _Graph: # pylint: disable=too-few-public-methods

    def __init__(self, metadata, graph):
        self.metadata = metadata
        self.value = graph
        self.nodes = []

    def to_json(self): # pylint: disable=missing-function-docstring,too-many-locals
        import torch # pylint: disable=import-outside-toplevel
        graph = self.value
        json_graph = {
            'arguments': [],
            'nodes': [],
            'inputs': [],
            'outputs': []
        }
        data_type_map = dict([
            [ torch.float16, 'float16'], # pylint: disable=no-member
            [ torch.float32, 'float32'], # pylint: disable=no-member
            [ torch.float64, 'float64'], # pylint: disable=no-member
            [ torch.int32, 'int32'], # pylint: disable=no-member
            [ torch.int64, 'int64'], # pylint: disable=no-member
        ])
        arguments_map = {}
        def argument(value):
            if not value in arguments_map:
                json_argument = {}
                json_argument['name'] = str(value.unique()) + '>' + str(value.node().kind())
                if value.isCompleteTensor():
                    json_tensor_shape = {
                        'dimensions': value.type().sizes()
                    }
                    json_argument['type'] = {
                        'dataType': data_type_map[value.type().dtype()],
                        'shape': json_tensor_shape
                    }
                if value.node().kind() == "prim::Param":
                    json_argument['initializer'] = {}
                arguments = json_graph['arguments']
                arguments_map[value] = len(arguments)
                arguments.append(json_argument)
            return arguments_map[value]

        for _ in graph.inputs():
            # if len(_.uses()) == 0:
            #     continue
            json_graph['inputs'].append({
                'name': _.debugName(),
                'arguments': [ argument(_) ]
            })
        for _ in graph.outputs():
            json_graph['outputs'].append({
                'name': _.debugName(),
                'arguments': [ argument(_) ]
            })
        for node in graph.nodes():
            # if node.kind() == 'prim::ListConstruct':
            #     continue
            # if node.kind() == 'prim::Constant':
            #     continue
            # if node.kind() == 'prim::GetAttr':
            #     continue
            schema = node.schema() if hasattr(node, 'schema') else None
            schema = self.metadata.type(schema) if schema and schema != '(no schema)' else None
            json_node = {
                'type': { 'name': node.kind() },
                'inputs': [],
                'outputs': [],
                'attributes': []
            }
            json_graph['nodes'].append(json_node)
            for name in node.attributeNames():
                value = node[name]
                json_attribute = {
                    'name': name,
                    'value': value
                }
                if torch.is_tensor(value):
                    json_node['inputs'].append({
                        'name': name,
                        'arguments': []
                    })
                else:
                    json_node['attributes'].append(json_attribute)

            for i, input_value in enumerate(node.inputs()):
                input_schema = schema['inputs'][i] if schema and i < len(schema['inputs']) else None
                name = input_schema['name'] if hasattr(input_schema, 'name') else 'input'
                json_parameter = {
                    'name': name,
                    'arguments': [ argument(input_value) ]
                }
                json_node['inputs'].append(json_parameter)

            for output_value in node.outputs():
                json_node['outputs'].append({
                    'name': 'x',
                    'arguments': [ argument(output_value) ]
                })
        return json_graph

class Metadata: # pylint: disable=too-few-public-methods,missing-class-docstring

    def __init__(self, metadata):
        self.types = metadata
        self.cache = set()

    def type(self, schema): # pylint: disable=missing-function-docstring
        key = schema.name if isinstance(schema, Schema) else schema.split('(', 1)[0].strip()
        if key not in self.cache and key != 'aten::as_tensor':
            self.cache.add(key)
            schema = schema if isinstance(schema, Schema) else Schema(schema)
            arguments = list(filter(lambda _: \
                not(_.kwarg_only and hasattr(_, 'alias')), schema.arguments))
            returns = schema.returns
            value = self.types.get(schema.name, { 'name': schema.name, })
            inputs = value.get('inputs', [])
            outputs = value.get('outputs', [])
            inputs = [ inputs[i] if i < len(inputs) else {} for i in range(len(arguments)) ]
            outputs = [ outputs[i] if i < len(outputs) else {} for i in range(len(returns)) ]
            value['inputs'] = inputs
            value['outputs'] = outputs
            for i, _ in enumerate(arguments):
                argument = inputs[i]
                argument['name'] = _.name
                self._argument(argument, getattr(_, 'type'))
                if hasattr(_, 'default'):
                    argument['default'] = _.default
            for i, _ in enumerate(returns):
                argument = outputs[i]
                if hasattr(_, 'name'):
                    argument['name'] = _.name
                self._argument(argument, getattr(_, 'type'))
        return self.types[key]

    def _argument(self, argument, value):
        optional = False
        argument_type = ''
        while not isinstance(value, str):
            if isinstance(value, Schema.OptionalType):
                value = value.element_type
                optional = True
            elif isinstance(value, Schema.ListType):
                size = str(value.size) if hasattr(value, 'size') else ''
                argument_type = '[' + size + ']' + argument_type
                value = value.element_type
            else:
                name = value.name
                if name == 'int':
                    name = 'int64'
                elif name == 'float':
                    name = 'float32'
                elif name == 'bool':
                    name = 'boolean'
                elif name == 'str':
                    name = 'string'
                argument_type = name + argument_type
                break
        if argument_type:
            argument['type'] = argument_type
        else:
            argument.pop('type', None)
        if optional:
            argument['optional'] = True
        else:
            argument.pop('optional', False)

class Schema: # pylint: disable=too-few-public-methods,missing-class-docstring
    def __init__(self, value):
        lexer = Schema.Lexer(value)
        lexer.whitespace(0)
        self._parse_name(lexer)
        lexer.whitespace(0)
        if lexer.kind == '(':
            self._parse_arguments(lexer)
            lexer.whitespace(0)
            lexer.expect('->')
            lexer.whitespace(0)
            self._parse_returns(lexer)
    def __str__(self):
        arguments = []
        kwarg_only = False
        for _ in self.arguments:
            if not kwarg_only and _.kwarg_only:
                kwarg_only = True
                arguments.append('*')
            arguments.append(_.__str__())
        if self.is_vararg:
            arguments.append('...')
        returns = ', '.join(map(lambda _: _.__str__(), self.returns))
        returns = returns if len(self.returns) == 1 else '(' + returns + ')'
        return self.name + '(' + ', '.join(arguments) + ') -> ' + returns
    def _parse_name(self, lexer):
        self.name = lexer.expect('id')
        if lexer.eat(':'):
            lexer.expect(':')
            self.name = self.name + '::' + lexer.expect('id')
        if lexer.eat('.'):
            self.name = self.name + '.' + lexer.expect('id')
    def _parse_arguments(self, lexer):
        self.arguments = []
        self.is_vararg = False
        self.kwarg_only = False
        lexer.expect('(')
        if not lexer.eat(')'):
            while True:
                lexer.whitespace(0)
                if self.is_vararg:
                    raise Exception()
                if lexer.eat('*'):
                    self.kwarg_only = True
                elif lexer.eat('...'):
                    self.is_vararg = True
                else:
                    self.arguments.append(Schema.Argument(lexer, False, self.kwarg_only))
                lexer.whitespace(0)
                if not lexer.eat(','):
                    break
            lexer.expect(')')
    def _parse_returns(self, lexer):
        self.returns = []
        self.is_varret = False
        if lexer.eat('...'):
            self.is_varret = True
        elif lexer.eat('('):
            lexer.whitespace(0)
            if not lexer.eat(')'):
                while True:
                    lexer.whitespace(0)
                    if self.is_varret:
                        raise Exception()
                    if lexer.eat('...'):
                        self.is_varret = True
                    else:
                        self.returns.append(Schema.Argument(lexer, True, False))
                    lexer.whitespace(0)
                    if not lexer.eat(','):
                        break
                lexer.expect(')')
            lexer.whitespace(0)
        else:
            self.returns.append(Schema.Argument(lexer, True, False))
    class Argument: # pylint: disable=too-few-public-methods
        def __init__(self, lexer, is_return, kwarg_only):
            value = Schema.Type(lexer)
            lexer.whitespace(0)
            while True:
                if lexer.eat('['):
                    size = None
                    if lexer.kind == '#':
                        size = int(lexer.value)
                        lexer.next()
                    lexer.expect(']')
                    value = Schema.ListType(value, size)
                elif lexer.eat('?'):
                    value = Schema.OptionalType(value)
                elif lexer.kind == '(' and not hasattr(self, 'alias'):
                    self.alias = self._parse_alias(lexer)
                else:
                    break
            self.type = value
            if is_return:
                lexer.whitespace(0)
                self.kwarg_only = False
                if lexer.kind == 'id':
                    self.name = lexer.expect('id')
            else:
                lexer.whitespace(1)
                self.kwarg_only = kwarg_only
                self.name = lexer.expect('id')
                lexer.whitespace(0)
                if lexer.eat('='):
                    lexer.whitespace(0)
                    self.default = self._parse_value(lexer)
        def __str__(self):
            alias = '(' + self.alias + ')' if hasattr(self, 'alias') else ''
            name = ' ' + self.name if hasattr(self, 'name') else ''
            default = '=' + self.default.__str__() if hasattr(self, 'default') else ''
            return self.type.__str__() + alias + name + default
        def _parse_value(self, lexer):
            if lexer.kind == 'id':
                if lexer.value in ('True', 'False'):
                    value = bool(lexer.value == 'True')
                elif lexer.value == 'None':
                    value = None
                elif lexer.value in ('Mean', 'contiguous_format', 'long'):
                    value = lexer.value
                else:
                    raise Exception()
            elif lexer.kind == '#':
                value = float(lexer.value) if \
                    lexer.value.find('.') != -1 or lexer.value.find('e') != -1 else \
                    int(lexer.value)
            elif lexer.kind == 'string':
                value = lexer.value[1:-1]
            elif lexer.eat('['):
                value = []
                if not lexer.eat(']'):
                    while True:
                        lexer.whitespace(0)
                        value.append(self._parse_value(lexer))
                        lexer.whitespace(0)
                        if not lexer.eat(','):
                            break
                    lexer.expect(']')
                return value
            else:
                raise Exception()
            lexer.next()
            return value
        def _parse_alias(self, lexer):
            value = ''
            lexer.expect('(')
            while not lexer.eat(')'):
                value += lexer.value
                lexer.next()
            return value
    class Type: # pylint: disable=too-few-public-methods,missing-class-docstring
        def __init__(self, lexer):
            self.name = lexer.expect('id')
            while lexer.eat('.'):
                self.name = self.name + '.' + lexer.expect('id')
        def __str__(self):
            return self.name
    class OptionalType: # pylint: disable=too-few-public-methods,missing-class-docstring
        def __init__(self, element_type):
            self.element_type = element_type
        def __str__(self):
            return self.element_type.__str__() + '?'
    class ListType: # pylint: disable=too-few-public-methods,missing-class-docstring
        def __init__(self, element_type, size):
            self.element_type = element_type
            if size:
                self.size = size
        def __str__(self):
            size = self.size.__str__() if hasattr(self, 'size') else ''
            return self.element_type.__str__() + '[' + size + ']'
    class Lexer: # pylint: disable=too-few-public-methods,missing-class-docstring
        def __init__(self, buffer):
            self.buffer = buffer
            self.position = 0
            self.value = ''
            self.next()
        def eat(self, kind): # pylint: disable=missing-function-docstring
            if self.kind != kind:
                return None
            value = self.value
            self.next()
            return value
        def expect(self, kind): # pylint: disable=missing-function-docstring
            if self.kind != kind:
                raise Exception("Unexpected '" + self.kind + "' instead of '" + kind + "'.")
            value = self.value
            self.next()
            return value
        def whitespace(self, count): # pylint: disable=missing-function-docstring
            if self.kind != ' ':
                if count > len(self.value):
                    raise Exception('')
                return False
            self.next()
            return True
        def next(self): # pylint: disable=missing-function-docstring,too-many-branches
            self.position += len(self.value)
            i = self.position
            if i >= len(self.buffer):
                self.kind = '\0'
                self.value = ''
            elif self.buffer[i] == ' ':
                while self.buffer[i] == ' ':
                    i += 1
                self.kind = ' '
                self.value = self.buffer[self.position:i]
            elif self.buffer[i] == '.' and self.buffer[i+1] == '.' and self.buffer[i+2] == '.':
                self.kind = '...'
                self.value = '...'
            elif self.buffer[i] in ('(', ')', ':', '.', '[', ']', ',', '=', '?', '!', '*', '|'):
                self.kind = self.buffer[i]
                self.value = self.buffer[i]
            elif (self.buffer[i] >= 'a' and self.buffer[i] <= 'z') or \
                 (self.buffer[i] >= 'A' and self.buffer[i] <= 'Z') or self.buffer[i] == '_':
                i += 1
                while i < len(self.buffer) and \
                    ((self.buffer[i] >= 'a' and self.buffer[i] <= 'z') or \
                     (self.buffer[i] >= 'A' and self.buffer[i] <= 'Z') or \
                     (self.buffer[i] >= '0' and self.buffer[i] <= '9') or self.buffer[i] == '_'):
                    i += 1
                self.kind = 'id'
                self.value = self.buffer[self.position:i]
            elif self.buffer[i] == '-' and self.buffer[i+1] == '>':
                self.kind = '->'
                self.value = '->'
            elif (self.buffer[i] >= '0' and self.buffer[i] <= '9') or self.buffer[i] == '-':
                i += 1
                while i < len(self.buffer) and \
                    ((self.buffer[i] >= '0' and self.buffer[i] <= '9') or \
                    self.buffer[i] == '.' or self.buffer[i] == 'e' or self.buffer[i] == '-'):
                    i += 1
                self.kind = '#'
                self.value = self.buffer[self.position:i]
            elif self.buffer[i] in ("'", '"'):
                quote = self.buffer[i]
                i += 1
                while i < len(self.buffer) and self.buffer[i] != quote:
                    i += 2 if self.buffer[i] == '\\' and self.buffer[i+1] in ("'", '"', '\\') else 1
                i += 1
                self.kind = 'string'
                self.value = self.buffer[self.position:i]
            else:
                raise Exception("Unsupported token at " + self.position)
