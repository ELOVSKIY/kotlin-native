#!/usr/bin/python

##
# Copyright 2010-2017 JetBrains s.r.o.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

#
# (lldb) command script import llvmDebugInfoC/src/scripts/konan_lldb.py
# (lldb) p kotlin_variable
#

import lldb
import struct
import re
import sys
import os
import time

NULL = 'null'
_CACHE = {}

def log(msg):
    if False:
        print(msg(), file=sys.stderr)
        exelog(msg)

def exelog(stmt):
    if False:
        f = open(os.getenv('HOME', '') + "/lldbexelog.txt", "a")
        f.write(stmt())
        f.write("\n")
        f.close()

def bench(start, msg):
    if True:
        print("{}: {}".format(msg(), time.monotonic() - start))

def evaluate(expr):
    result = lldb.debugger.GetSelectedTarget().EvaluateExpression(expr)
    log(lambda : "evaluate: {} => {}".format(expr, result))
    return result

def _symbol_loaded_address(name, debugger = lldb.debugger):
    target = debugger.GetSelectedTarget()
    process = target.GetProcess()
    thread = process.GetSelectedThread()
    frame = thread.GetSelectedFrame()
    candidates = list(filter(lambda x: x.name == name, frame.module.symbols))
    # take first
    for candidate in candidates:
        address = candidate.GetStartAddress().GetLoadAddress(target)
        log(lambda: "_symbol_loaded_address:{} {:#x}".format(name, address))
        return address

def _type_info_by_address(address, debugger = lldb.debugger):
    target = debugger.GetSelectedTarget()
    process = target.GetProcess()
    thread = process.GetSelectedThread()
    frame = thread.GetSelectedFrame()
    candidates = list(filter(lambda x: x.GetStartAddress().GetLoadAddress(target) == address, frame.module.symbols))
    return candidates

def is_instance_of(addr, typeinfo):
    return evaluate("(bool)IsInstance({:#x}, {:#x})".format(addr, typeinfo)).GetValue() == "true"

def is_string_or_array(value):
    start = time.monotonic()
    soa = evaluate("(int)IsInstance({0:#x}, {1:#x}) ? 1 : ((int)Konan_DebugIsArray({0:#x})) ? 2 : 0)".format(value.unsigned, _symbol_loaded_address('kclass:kotlin.String'))).unsigned
    log(lambda: "is_string_or_array:{:#x}:{}".format(value.unsigned, soa))
    bench(start, lambda: f"is_string_or_array({value.unsigned:#x}) = {soa}")
    return soa

def type_info(value):
    """This method checks self-referencing of pointer of first member of TypeInfo including case when object has an
    meta-object pointed by TypeInfo. Two lower bits are reserved for memory management needs see runtime/src/main/cpp/Memory.h."""
    log(lambda: "type_info({:#x}: {})".format(value.unsigned, value.GetTypeName()))
    if value.GetTypeName() != "ObjHeader *":
        return None
    expr = "*(void **)((uintptr_t)(*(void**){0:#x}) & ~0x3) == **(void***)((uintptr_t)(*(void**){0:#x}) & ~0x3) ? *(void **)((uintptr_t)(*(void**){0:#x}) & ~0x3) : (void *)0".format(value.unsigned)
    result = evaluate(expr)

    return result.unsigned if result.IsValid() and result.unsigned != 0 else None


__FACTORY = {}


# Cache type info pointer to [ChildMetaInfo]
SYNTHETIC_OBJECT_LAYOUT_CACHE = {}
TO_STRING_DEPTH = 2
ARRAY_TO_STRING_LIMIT = 10

def kotlin_object_type_summary(lldb_val, internal_dict = {}):
    """Hook that is run by lldb to display a Kotlin object."""
    start = time.monotonic()
    log(lambda: f"kotlin_object_type_summary({lldb_val.unsigned:#x}: {lldb_val.GetTypeName()})")
    fallback = lldb_val.GetValue()
    if lldb_val.GetTypeName() != "ObjHeader *":
        if lldb_val.GetValue() is None:
            bench(start, lambda: "kotlin_object_type_summary:({:#x}) = NULL".format(lldb_val.unsigned))
            return NULL
        bench(start, lambda: "kotlin_object_type_summary:({:#x}) = {}".format(lldb_val.unsigned, lldb_val.signed))
        return lldb_val.signed

    if lldb_val.unsigned == 0:
            bench(start, lambda: "kotlin_object_type_summary:({:#x}) = NULL".format(lldb_val.unsigned))
            return NULL
    tip = internal_dict["type_info"] if "type_info" in internal_dict.keys() else type_info(lldb_val)

    if not tip:
        bench(start, lambda: "kotlin_object_type_summary:({0:#x}) = falback:{0:#x}".format(lldb_val.unsigned))
        return fallback

    value = select_provider_cached(lldb_val, tip, internal_dict)
    bench(start, lambda: "kotlin_object_type_summary:({:#x}) = value:{:#x}".format(lldb_val.unsigned, value._valobj.unsigned))
    start = time.monotonic()
    str0 = str(value.to_string())
    bench(start, lambda: "kotlin_object_type_summary:({:#x}) = str:'{}...'".format(lldb_val.unsigned, str0[:3]))
    return str0

def select_provider_cached(lldb_val, tip, internal_dict):
    start = time.monotonic()
    log(lambda : "select_provider_cached: {:#x} name:{} tip:{:#x}".format(lldb_val.unsigned, lldb_val.name, tip))
    
    ret = _CACHE["f{lldb_val.unsigned:#x}"] if "f{lldb_val.unsigned:#x}" in _CACHE.keys() else select_provider(lldb_val, tip, internal_dict)
    bench(start, lambda: "select_provider_cached({:#x})".format(lldb_val.unsigned))
    return ret

def select_provider(lldb_val, tip, internal_dict):
    start = time.monotonic()
    log(lambda : "select_provider: {:#x} name:{} tip:{:#x}".format(lldb_val.unsigned, lldb_val.name, tip))
    if "{lldb_val.unsigned:#x}" in _CACHE.keys():
        ret = _CACHE["{lldb_val.unsigned:#x}"]
        bench(start, lambda: "select_provider({:#x}) ff".format(lldb_val.unsigned))
        return ret

    soa = is_string_or_array(lldb_val)
    log(lambda : "select_provider: {:#x} : soa: {}".format(lldb_val.unsigned, soa))
    ret =   __FACTORY['string'](lldb_val, tip, internal_dict) if soa == 1 else __FACTORY['array'](lldb_val, tip, internal_dict) if soa == 2 \
        else __FACTORY['object'](lldb_val, tip, internal_dict)
    bench(start, lambda: "select_provider({:#x})".format(lldb_val.unsigned))
    return ret

class KonanHelperProvider(lldb.SBSyntheticValueProvider):
    def __init__(self, valobj, amString, internal_dict = {}):
        self._target = lldb.debugger.GetSelectedTarget()
        self._process = self._target.GetProcess()
        self._valobj = valobj
        self._internal_dict = internal_dict.copy()
        _CACHE[f"{valobj.unsigned:#x}"] = self
        if amString:
            return
        self._to_string_depth = TO_STRING_DEPTH if "to_string_depth" not in self._internal_dict.keys() else  self._internal_dict["to_string_depth"]
        if self._children_count == 0:
            children_count = evaluate("(int)Konan_DebugGetFieldCount({:#x})".format(self._valobj.unsigned)).signed
            log(lambda: "(int)[{}].Konan_DebugGetFieldCount({:#x}) = {}".format(self._valobj.name, self._valobj.unsigned, children_count))
            self._children_count = children_count
        self._children = []
        self._type_conversion = [
            lambda address, name: self._valobj.CreateValueFromExpression(name, "(void *){:#x}".format(address)),
            lambda address, name: self._create_synthetic_child(address, name),
            lambda address, name: self._valobj.CreateValueFromExpression(name, "(int8_t *){:#x}".format(address)),
            lambda address, name: self._valobj.CreateValueFromExpression(name, "(int16_t *){:#x}".format(address)),
            lambda address, name: self._valobj.CreateValueFromExpression(name, "(int32_t *){:#x}".format(address)),
            lambda address, name: self._valobj.CreateValueFromExpression(name, "(int64_t *){:#x}".format(address)),
            lambda address, name: self._valobj.CreateValueFromExpression(name, "(float *){:#x}".format(address)),
            lambda address, name: self._valobj.CreateValueFromExpression(name, "(double *){:#x}".format(address)),
            lambda address, name: self._valobj.CreateValueFromExpression(name, "(void **){:#x}".format(address)),
            lambda address, name: self._valobj.CreateValueFromExpression(name, "(bool *){:#x}".format(address)),
            lambda address, name: None]

        self._types = [
            valobj.GetType().GetBasicType(lldb.eBasicTypeVoid).GetPointerType(),
            valobj.GetType(),
            valobj.GetType().GetBasicType(lldb.eBasicTypeChar),
            valobj.GetType().GetBasicType(lldb.eBasicTypeShort),
            valobj.GetType().GetBasicType(lldb.eBasicTypeInt),
            valobj.GetType().GetBasicType(lldb.eBasicTypeLongLong),
            valobj.GetType().GetBasicType(lldb.eBasicTypeFloat),
            valobj.GetType().GetBasicType(lldb.eBasicTypeDouble),
            valobj.GetType().GetBasicType(lldb.eBasicTypeVoid).GetPointerType(),
            valobj.GetType().GetBasicType(lldb.eBasicTypeBool)
        ]

    def _read_string(self, expr, error):
        return self._process.ReadCStringFromMemory(evaluate(expr).unsigned, 0x1000, error)

    def _read_value(self, index):
        value_type = self._children[index].type()
        address = self._valobj.unsigned + self._children[index].offset()
        log(lambda: "_read_value: [{}, type:{}, address:{:#x}]".format(index, value_type, address))
        return self._type_conversion[int(value_type)](address, str(self._children[index].name()))

    def _create_synthetic_child(self, address, name):
        index = self.get_child_index(name)
        log(lambda: "_create_synthetic_child({:#x}, {:#x}, {}):_to_string_depth:{}".format(self._valobj.unsigned, address, name, self._to_string_depth))
        if self._to_string_depth == 0:
           return None
        log(lambda: "_create_synthetic_child: [index:{}, {}: {:#x} value:{:#x}]".format(index, name, address, evaluate("*(void**){:#x}".format(address)).unsigned))
        value = self._valobj.CreateChildAtOffset(str(name),
                                                 self._children[index].offset(),
                                                 self._read_type(index))
        value.SetSyntheticChildrenGenerated(True)
        value.SetPreferSyntheticValue(True)
        return value

    def _read_type(self, index):
        type = self._types[self._children[index].type()]
        log(lambda: "type:{0} of {1:#x} of {2:#x}".format(type, self._valobj.unsigned, self._valobj.unsigned + self._children[index].offset()))
        return type

    def _deref_or_obj_summary(self, index, internal_dict):
        value = self._values[index]
        if not value:
            log(lambda : "_deref_or_obj_summary: value none, index:{}, type:{}".format(index, self._children[index].type()))
            return None

        tip = type_info(value)
        if tip:
            internal_dict["type_info"] = tip
            return kotlin_object_type_summary(value, internal_dict)
        tip = type_info(value.deref)

        if tip:
            internal_dict["type_info"] = tip
            return kotlin_object_type_summary(value.deref, internal_dict)

        return kotlin_object_type_summary(value.deref, internal_dict)

    def _field_address(self, index):
        return evaluate("(void *)Konan_DebugGetFieldAddress({:#x}, {})".format(self._valobj.unsigned, index)).unsigned

    def _field_type(self, index):
        return evaluate("(int)Konan_DebugGetFieldType({:#x}, {})".format(self._valobj.unsigned, index)).unsigned

class KonanStringSyntheticProvider(KonanHelperProvider):
    def __init__(self, valobj):
        log(lambda: "KonanStringSyntheticProvider:{:#x} name:{}".format(valobj.unsigned, valobj.name))
        self._children_count = 0
        super(KonanStringSyntheticProvider, self).__init__(valobj, True)
        fallback = valobj.GetValue()
        buff_addr = evaluate("(void *)Konan_DebugBuffer()").unsigned
        buff_len = evaluate(
            '(int)Konan_DebugObjectToUtf8Array({:#x}, (void *){:#x}, (int)Konan_DebugBufferSize());'.format(
                self._valobj.unsigned, buff_addr)
        ).signed

        if not buff_len:
            self._representation = fallback
            return

        error = lldb.SBError()
        s = self._process.ReadCStringFromMemory(int(buff_addr), int(buff_len), error)
        if not error.Success():
            raise DebuggerException()
        self._representation = s if error.Success() else fallback
        self._logger = lldb.formatters.Logger.Logger()

    def update(self):
        pass

    def num_children(self):
        return 0

    def has_children(self):
        return False

    def get_child_index(self, _):
        return None

    def get_child_at_index(self, _):
        return None

    def to_string(self):
        return self._representation


class DebuggerException(Exception):
    pass

class MemberLayout:
    def __init__(self, name, type, offset):
        self._name = name
        self._type = type
        self._offset = offset

    def name(self):
        return self._name

    def type(self):
        return self._type

    def offset(self):
        return self._offset

class KonanObjectSyntheticProvider(KonanHelperProvider):
    def __init__(self, valobj, tip, internal_dict):
        # Save an extra call into the process
        if tip in SYNTHETIC_OBJECT_LAYOUT_CACHE:
            log(lambda : "TIP: {:#x} EARLYHIT".format(tip))
            self._children = SYNTHETIC_OBJECT_LAYOUT_CACHE[tip]
            self._children_count = len(self._children)
        else:
            self._children_count = 0

        super(KonanObjectSyntheticProvider, self).__init__(valobj, False, internal_dict)

        if not tip in SYNTHETIC_OBJECT_LAYOUT_CACHE:
            SYNTHETIC_OBJECT_LAYOUT_CACHE[tip] = [
                MemberLayout(self._field_name(i), self._field_type(i), self._field_address(i) - self._valobj.unsigned)
                for i in range(self._children_count)]
            log(lambda : "TIP: {:#x} MISSED".format(tip))
        else:
            log(lambda : "TIP: {:#x} HIT".format(tip))
        self._children = SYNTHETIC_OBJECT_LAYOUT_CACHE[tip]
        self._values = [self._read_value(index) for index in range(self._children_count)]


    def _field_name(self, index):
        error = lldb.SBError()
        name =  self._read_string("(void *)Konan_DebugGetFieldName({:#x}, (int){})".format(self._valobj.unsigned, index), error)
        if not error.Success():
            raise DebuggerException()
        return name

    def num_children(self):
        return self._children_count

    def has_children(self):
        return self._children_count > 0

    def get_child_index(self, name):
        def __none(iterable, f):
            return not any(f(x) for x in iterable)
        if __none(self._children, lambda x: x.name() == name):
            return -1
        return next(i for i,v in enumerate(self._children) if v.name() == name)

    def get_child_at_index(self, index):
        result = self._values[index]
        if result is None:
            result = self._read_value(index)
            self._values[index] = result
        return result

    # TODO: fix cyclic structures stringification.
    def to_string(self):
        log(lambda:"to_string: {:#x}: _to_string_depth:{}".fromat(self._valobj.unsigned, self._to_string_depth))
        if self._to_string_depth == 0:
            return "..."
        else:
            internal_dict = self._internal_dict.copy()
            internal_dict["to_string_depth"] = self._to_string_depth - 1
            return dict([(self._children[i].name(), self._deref_or_obj_summary(i, internal_dict)) for i in range(self._children_count)])

class KonanArraySyntheticProvider(KonanHelperProvider):
    def __init__(self, valobj, internal_dict):
        self._children_count = 0
        super(KonanArraySyntheticProvider, self).__init__(valobj, False, internal_dict)
        log(lambda: "KonanArraySyntheticProvider: valobj:{:#x}".format(valobj.unsigned))
        if self._valobj is None:
            return
        valobj.SetSyntheticChildrenGenerated(True)
        type = self._field_type(0)
        zerro_address = self._field_address(0)
        first_address = self._field_address(1)
        offset = zerro_address - valobj.unsigned
        size = first_address - zerro_address
        log(lambda: "KonanArraySyntheticProvider: offest:{:#x}, size:{}".format(offset, size))
        self._children = [MemberLayout(str(x), type, offset + x * size) for x in range(self.num_children())]
        self._values = [self._read_value(i) for i in range(min(ARRAY_TO_STRING_LIMIT, self._children_count))]

    def cap_children_count(self):
        return self._children_count

    def num_children(self):
        return self.cap_children_count()

    def has_children(self):
        return self._children_count > 0

    def get_child_index(self, name):
        index = int(name)
        return index if (0 <= index < self._children_count) else -1

    def get_child_at_index(self, index):
        result = self._values[index]
        if result is None:
            result = self._read_value(index)
            self._values[index] = result
        return result

    def to_string(self):
        internal_dict = self._internal_dict.copy()
        internal_dict["to_string_depth"] = self._to_string_depth - 1
        return [self._deref_or_obj_summary(i, internal_dict) for i in range(min(ARRAY_TO_STRING_LIMIT, self._children_count))]


class KonanProxyTypeProvider:
    def __init__(self, valobj, internal_dict):
        start = time.monotonic()
        log(lambda : "KonanProxyTypeProvider:{:#x}, name: {}".format(valobj.unsigned, valobj.name))
        tip = type_info(valobj)

        if not tip:
            return
        log(lambda : "KonanProxyTypeProvider:{:#x} tip: {:#x}".format(valobj.unsigned, tip))
        self._proxy = select_provider_cached(valobj, tip, internal_dict)
        bench(start, lambda: "KonanProxyTypeProvider({:#x})".format(valobj.unsigned))
        log(lambda: "KonanProxyTypeProvider:{:#x} _proxy: {}".format(valobj.unsigned, self._proxy.__class__.__name__))
        self.update()

    def __getattr__(self, item):
        return getattr(self._proxy, item)

def clear_cache_command(debugger, command, result, internal_dict):
    SYNTHETIC_OBJECT_LAYOUT_CACHE.clear()


def type_name_command(debugger, command, result, internal_dict):
    result.AppendMessage(evaluate('(char *)Konan_DebugGetTypeName({})'.format(command)).summary)

__KONAN_VARIABLE = re.compile('kvar:(.*)#internal')
__KONAN_VARIABLE_TYPE = re.compile('^kfun:<get-(.*)>\\(\\)(.*)$')
__TYPES_KONAN_TO_C = {
   'kotlin.Byte': ('int8_t', lambda v: v.signed),
   'kotlin.Short': ('short', lambda v: v.signed),
   'kotlin.Int': ('int', lambda v: v.signed),
   'kotlin.Long': ('long', lambda v: v.signed),
   'kotlin.UByte': ('int8_t', lambda v: v.unsigned),
   'kotlin.UShort': ('short', lambda v: v.unsigned),
   'kotlin.UInt': ('int', lambda v: v.unsigned),
   'kotlin.ULong': ('long', lambda v: v.unsigned),
   'kotlin.Char': ('short', lambda v: v.signed),
   'kotlin.Boolean': ('bool', lambda v: v.signed),
   'kotlin.Float': ('float', lambda v: v.value),
   'kotlin.Double': ('double', lambda v: v.value)
}

def type_by_address_command(debugger, command, result, internal_dict):
    result.AppendMessage("DEBUG: {}".format(command))
    tokens = command.split()
    target = debugger.GetSelectedTarget()
    process = target.GetProcess()
    thread = process.GetSelectedThread()
    types = _type_info_by_address(tokens[0])
    result.AppendMessage("DEBUG: {}".format(types))
    for t in types:
        result.AppendMessage("{}: {:#x}".format(t.name, t.GetStartAddress().GetLoadAddress(target)))

def symbol_by_name_command(debugger, command, result, internal_dict):
    target = debugger.GetSelectedTarget()
    process = target.GetProcess()
    thread = process.GetSelectedThread()
    frame = thread.GetSelectedFrame()
    tokens = command.split()
    mask = re.compile(tokens[0])
    symbols = list(filter(lambda v: mask.match(v.name), frame.GetModule().symbols))
    visited = list()
    for symbol in symbols:
       name = symbol.name
       if name in visited:
           continue
       visited.append(name)
       result.AppendMessage("{}: {:#x}".format(name, symbol.GetStartAddress().GetLoadAddress(target)))

def konan_globals_command(debugger, command, result, internal_dict):
    target = debugger.GetSelectedTarget()
    process = target.GetProcess()
    thread = process.GetSelectedThread()
    frame = thread.GetSelectedFrame()

    konan_variable_symbols = list(filter(lambda v: __KONAN_VARIABLE.match(v.name), frame.GetModule().symbols))
    visited = list()
    for symbol in konan_variable_symbols:
       name = __KONAN_VARIABLE.search(symbol.name).group(1)

       if name in visited:
           continue
       visited.append(name)

       getters = list(filter(lambda v: re.match('^kfun:<get-{}>\\(\\).*$'.format(name), v.name), frame.module.symbols))
       if not getters:
           result.AppendMessage("storage not found for name:{}".format(name))
           continue

       getter_functions = frame.module.FindFunctions(getters[0].name)
       if not getter_functions:
           continue

       address = getter_functions[0].function.GetStartAddress().GetLoadAddress(target)
       type = __KONAN_VARIABLE_TYPE.search(getters[0].name).group(2)
       (c_type, extractor) = __TYPES_KONAN_TO_C[type] if type in __TYPES_KONAN_TO_C.keys() else ('ObjHeader *', lambda v: kotlin_object_type_summary(v))
       value = evaluate('(({0} (*)()){1:#x})()'.format(c_type, address))
       str_value = extractor(value)
       result.AppendMessage('{} {}: {}'.format(type, name, str_value))

def __lldb_init_module(debugger, _):
    log(lambda: "init start")
    __FACTORY['object'] = lambda x, y, z: KonanObjectSyntheticProvider(x, y, z)
    __FACTORY['array'] = lambda x, y, z: KonanArraySyntheticProvider(x, z)
    __FACTORY['string'] = lambda x, y, _: KonanStringSyntheticProvider(x)
    debugger.HandleCommand('\
        type summary add \
        --no-value \
        --expand \
        --python-function konan_lldb.kotlin_object_type_summary \
        "ObjHeader *" \
        --category Kotlin\
    ')
    debugger.HandleCommand('\
        type synthetic add \
        --python-input konan_lldb.select_provider_cached \
        "ObjHeader *" \
        --category Kotlin\
    ')
    debugger.HandleCommand('type category enable Kotlin')
    debugger.HandleCommand('command script add -f {}.clear_cache_command clear_kotlin_cache'.format(__name__))
    debugger.HandleCommand('command script add -f {}.type_name_command type_name'.format(__name__))
    debugger.HandleCommand('command script add -f {}.type_by_address_command type_by_address'.format(__name__))
    debugger.HandleCommand('command script add -f {}.symbol_by_name_command symbol_by_name'.format(__name__))
    log(lambda: "init end")
