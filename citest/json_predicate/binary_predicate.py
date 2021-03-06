# Copyright 2015 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Binary predicates define a predicate relating the value to a fixed operand.

For example a comparator operation that compares a given value against a
reference point. The reference point would be the fixed operand, and the given
value would be the value that the base interface is given to apply the
predicate to.
"""


import inspect

from . import predicate
from .keyed_predicate_result import KeyedPredicateResultBuilder
from .map_predicate import MapPredicate
from .path_value import PathValue
from .path_predicate import PathPredicate
from .path_result import (
    MissingPathError,
    PathValueResult,
    TypeMismatchError,
    UnexpectedPathError)
from .sequenced_predicate_result import SequencedPredicateResultBuilder


class BinaryPredicate(predicate.ValuePredicate):
  """
  The base class for standard binary predicates.

  All the BinaryPredicates are constructed with an operand, which is the
  "operand" in the binary operator that you want to compare against.
  The other of operands is passed into the "__call__" method as a specific
  "value" to run the predicate against.

  For example, a predicate that checks for numbers < 10 would be constructed
  with an operand of 10: pred = INT_LT(10)
  Then to test a particular value, such as 5, you would pass 5 into the call
  operator (along with an evaluation context): result = pred(context, 5)

  Note that the operand bound into the predicate can itself be a callable if
  the desired wont be known until later. In this case, the callable will be
  invoked with an execution context which, presumably, will be populated using
  a pre-negotiated key (e.g. hardcoded) with the actual value to use.
     pred = INT_LT(lambda context: context['MyKey'])
     context['MyKey'] = 10
     result = pred(context, 5)

  Attributes:
    name: The name of the predicate is used to specify the particular
        comparison predicate to use. The supported names are:
    operand: The operand to compare against. This is the RHS of the predicate.
  """
  # pylint: disable=abstract-method

  @property
  def name(self):
    """The predicate name."""
    return self.__name

  @property
  def operand(self):
    """The fixed operand argument."""
    return self.__operand

  @property
  def operand_type(self):
    """The expected type of the operand."""
    return self.__operand_type

  def eval_context_operand(self, context):
    """Determine the operand type for the given evaluation context."""
    operand = context.eval(self.__operand)
    if self.__operand_type and not isinstance(operand, self.__operand_type):
      raise TypeError(
          '{0} is not {1}: {2!r}',
          operand.__class__, self.__operand_type, operand)
    return operand

  def __str__(self):
    if self.__operand_type is None:
      type_name = 'Any'
    elif inspect.isclass(self.__operand_type):
      type_name = self.__operand_type.__name__
    else:
      type_name = str(self.__operand_type)
    return '{0}({1!r})->{2}'.format(self.name, self.operand, type_name)

  def __init__(self, name, operand, **kwargs):
    self.__operand_type = kwargs.pop('operand_type', None)
    self.__name = name
    self.__operand = operand
    if self.__operand_type is not None and not callable(self.__operand):
      if not isinstance(self.__operand, self.__operand_type):
        raise TypeError(
            '{0} is not {1}: {2!r}',
            operand.__class__, self.__operand_type, operand)
    super(BinaryPredicate, self).__init__(**kwargs)

  def __eq__(self, pred):
    return (self.__class__ == pred.__class__
            and self.__name == pred.name
            and self.__operand == pred.operand
            and self.__operand_type == pred.operand_type)

  def export_to_json_snapshot(self, snapshot, entity):
    """Implements JsonSnapshotableEntity interface."""
    snapshot.edge_builder.make(entity, 'Name', self.__name)
    snapshot.edge_builder.make_control(entity, 'Operand', self.__operand)


class StandardBinaryPredicateFactory(object):
  """Create a StandardBinaryPredicate once we have an operand to bind to it."""
  # pylint: disable=too-few-public-methods

  @property
  def predicate_name(self):
    """The name of the predicate for reporting purposes."""
    return self.__name

  def __init__(self, name, comparison_op, **kwargs):
    """Constructor.

    Args:
      name: Name of the comparison_op
      comparison_op: Callable that takes (value, operand) and returns bool.
      operand_type: Class expected for operands, or None to not enforce.
    """
    self.__name = name
    self.__comparison_op = comparison_op
    self.__kwargs = dict(kwargs)

  def __call__(self, operand):
    return StandardBinaryPredicate(
        self.__name, self.__comparison_op, operand, **self.__kwargs)


class StandardBinaryPredicate(BinaryPredicate):
  """A BinaryPredicate using a bool predicate bound at construction."""

  def __init__(self, name, comparison_op, operand, **kwargs):
    """Constructor.

    Args:
      name: Name of predicate
      comparison_op: Implements bool predicate
      operand: Value to bind to predicate.

      See base class (BinaryPredicate) for additional kwargs.
    """
    super(StandardBinaryPredicate, self).__init__(name, operand, **kwargs)
    self.__comparison_op = comparison_op

  def __call__(self, context, value):
    operand = self.eval_context_operand(context)
    if self.operand_type and not isinstance(value, self.operand_type):
      return TypeMismatchError(self.operand_type, value.__class__, value)

    valid = self.__comparison_op(value, operand)
    return PathValueResult(pred=self, source=value, target_path='',
                           path_value=PathValue('', value), valid=valid)


class DictMatchesPredicate(BinaryPredicate):
  """Implements binary predicate comparison predicates against dict values.

  The dictionary value is in the form {field : pred} where each element
  in the dictionary is a predicate for validating that particular field.
  A strict predicate means that exactly all specified fields must be present.
  Otherwise this permits additional fields with arbitrary values.

  For example (using the json_predicate aliases EQUIVALENT and CONTAINS):
      DictMatcherPredicate({'n' : EQUIVALENT(10), 's' : CONTAINS('text')})
  would want a field with n=10 and s having 'text' as a substring.
  """

  @property
  def strict(self):
    """Whether only the specified fields may be present (True) or not."""
    return self.__strict

  def __init__(self, operand, **kwargs):
    """Constructor."""
    if not isinstance(operand, dict):
      raise TypeError(
          '{0} is not a dict: {1!r}'.format(operand.__class__, operand))
    self.__strict = kwargs.pop('strict', False)
    super(DictMatchesPredicate, self).__init__('Matches', operand, **kwargs)

  def export_to_json_snapshot(self, snapshot, entity):
    """Implements JsonSnapshotableEntity interface."""
    entity.add_metadata('strict', self.__strict)
    for key, pred in self.operand.items():
      snapshot.edge_builder.make_control(entity, key, pred)

  def __eq__(self, obj):
    return (super(DictMatchesPredicate, self).__eq__(obj)
            and self.__strict == obj.strict)

  def __call__(self, context, value):
    """Implements Predicate interface.

    context: [ExecutionContext] The execution context.
    value: [dict] The dictionary to match against our operand.

    Returns a KeyedPredicateResult indicating each of the fields.
    """
    if not isinstance(value, dict):
      return TypeMismatchError(dict, value.__class__, value)

    match_result_builder = KeyedPredicateResultBuilder(self)
    valid = True
    # pylint: disable=redefined-variable-type
    for key, pred in self.operand.items():
      name = context.eval(key)
      name_result = PathPredicate(key, pred, source_pred=pred,
                                  enumerate_terminals=False)(context, value)
      if not name_result:
        valid = False
      match_result_builder.add_result(name, name_result)

    if self.strict:
      # Only consider and add strictness result if it fails
      strictness_errors = self._find_unexpected_path_errors(context, value)
      if strictness_errors:
        valid = False
        match_result_builder.update_results(strictness_errors)

    return match_result_builder.build(valid)

  def _find_unexpected_path_errors(self, context, source):
    """Check value keys for unexpected ones.

    Args:
      context: [ExecutionContext] The execution context.
      source: [dict] The dictionary to match against our operand.

    Returns:
      dictionary of errors keyed by unexpected path.
    """
    # pylint: disable=unused-argument
    errors = {}
    expect_keys = self.operand.keys()
    for key, value in source.items():
      if key not in expect_keys:
        errors[key] = UnexpectedPathError(source=source, target_path=key,
                                          path_value=PathValue(key, value))
    return errors

class ListMatchesPredicate(BinaryPredicate):
  """Implements binary predicate comparison predicates against list values.

  Each element of the operand is a predicate that validates each element in the
  called value list. If the predicate is strict, then each value must match a
  predicate in the operand. If the predicate is unique then each value must only
  match one predicate.
  """

  @property
  def strict(self):
    """Whether all the elements must satisfy a predicate (True) or not."""
    return self.__strict

  @property
  def unique(self):
    """Whether a given element complies with at most one (True) predicates."""
    return self.__unique

  def __init__(self, operand, **kwargs):
    """Constructor."""
    if not isinstance(operand, list):
      raise TypeError(
          '{0} is not a list: {1!r}'.format(operand.__class__, operand))
    self.__strict = kwargs.pop('strict', False)
    self.__unique = kwargs.pop('unique', False)
    super(ListMatchesPredicate, self).__init__('Matches', operand, **kwargs)

  def __eq__(self, obj):
    return (super(ListMatchesPredicate, self).__eq__(obj)
            and self.__unique == obj.unique
            and self.__strict == obj.strict)

  def export_to_json_snapshot(self, snapshot, entity):
    """Implements JsonSnapshotableEntity interface."""
    entity.add_metadata('strict', self.__strict)
    entity.add_metadata('unique', self.__unique)
    for index, pred in enumerate(self.operand):
      key = '[{0}]'.format(index)
      snapshot.edge_builder.make_control(entity, key, pred)

  def __call__(self, context, value):
    """Implements Predicate interface.

    context: [ExecutionContext] The execution context.
    value: [list] The value list to match against our operand.

    Returns a SequencedPredicateResult indicating each of the fields.
    """
    if not isinstance(value, list):
      return TypeMismatchError(list, value.__class__, value)

    if self.__unique:
      max_count = 1
    else:
      max_count = None

    match_result_builder = SequencedPredicateResultBuilder(self)
    valid = True
    matched_element_count = [0] * len(value)
    # pylint: disable=redefined-variable-type
    for match_pred in self.operand:
      pred_result = MapPredicate(match_pred, max=max_count)(context, value)
      match_result_builder.append_result(pred_result)
      if not pred_result:
        valid = False
      for index in range(len(value)):
        matched_element_count[index] += 1 if pred_result.results[index] else 0

    if self.strict:
      # Only consider and add strictness result if it fails
      strictness_errors = self._find_strictness_errors(
          matched_element_count, value)
      if strictness_errors:
        valid = False
        match_result_builder.extend_results(strictness_errors)

    return match_result_builder.build(valid)

  def _find_strictness_errors(self, matched_element_count, source):
    """Check for each element being matched

    Args:
      matched_element_count: [list] number of matched predicates in source.
      source: [list] The list to match against our operand.

    Returns:
      list of unmatched source values.
    """
    # pylint: disable=unused-argument
    errors = []
    for index, count in enumerate(matched_element_count):
      if count == 0:
        path = '[{0}]'.format(index)
        errors.append(
            UnexpectedPathError(source=source, target_path=path,
                                path_value=PathValue(path, source[index])))
    return errors

class DictSubsetPredicate(BinaryPredicate):
  """Implements binary predicate comparison predicates against dict values."""

  def __init__(self, operand, **kwargs):
    if not (isinstance(operand, dict) or callable(operand)):
      raise TypeError(
          '{0} is not a dict: {1!r}'.format(operand.__class__, operand))
    super(DictSubsetPredicate, self).__init__('has-subset', operand,
                                              operand_type=dict, **kwargs)

  def __call__(self, context, value):
    if not isinstance(value, dict):
      return TypeMismatchError(dict, value.__class__, value)
    operand = self.eval_context_operand(context)
    return self._is_subset(context, value, '', operand, value)

  def _is_subset(self, context, source, path, a, b):
    """Determine if |a| is a subset of |b|.

    Args:
      context: [ExecutionContext]
      source: [obj] The JSON object containing |a|.
      path: [string] The path to |a| from |source|.
      a: |obj| The JSON object that should be a subset of |b|.
      b: |obj| The JSON object that shuld be a superset of |a|.
    """
    # pylint: disable=invalid-name

    ## FOR EACH element of operand...
    for name, a_value in a.items():
      namepath = '{0}/{1}'.format(path, name) if path else name
      try:
        b_value = b[name]
      except KeyError:
        ## IF element was not in |b| then it is not a subset.
        return MissingPathError(source=source, target_path=namepath,
                                path_value=PathValue(path, b))

      # IF the element is itself a dictionary
      # THEN recurse to ensure |a_item| is a subset of |b_item|.
      if isinstance(b_value, dict):
        result = self._is_subset(context, source, namepath, a_value, b_value)
        if not result:
          return result
        continue

      # IF the element is a list
      # THEN ensure that |a_item| is a subset of |b_item|.
      if isinstance(b_value, list):
        elem_pred = LIST_SUBSET if isinstance(a_value, list) else CONTAINS
        result = elem_pred(a_value)(context, b_value)
        if not result:
          return result.clone_with_source(
              source=source, base_target_path=namepath, base_value_path=name)
        continue

      # Up until now we never used a_value directly.
      a_value = context.eval(a_value)

      # Otherwise, we want an exact match.
      # Seems practical for what's intended.
      # If individual fields want different types of matches,
      # then they can call themselves out into a different PathFinder
      # that specifies the individual fields rather than a container.

      if a_value != b_value:
        # pylint: disable=redefined-variable-type
        if isinstance(b_value, basestring):
          pred_factory = STR_EQ
          confirm_type = basestring
        elif isinstance(b_value, (int, long, float)):
          pred_factory = NUM_EQ
          confirm_type = (int, long, float)
        else:
          pred_factory = EQUIVALENT
          confirm_type = None

        if confirm_type is not None and not isinstance(a_value, confirm_type):
          return TypeMismatchError(confirm_type, a_value.__class__,
                                   source=source, target_path=namepath,
                                   path_value=PathValue(path, b))
        return PathValueResult(
            pred=pred_factory(a_value), source=source, target_path=namepath,
            path_value=PathValue(namepath, b_value), valid=False)

    return PathValueResult(
        pred=self, source=source, target_path=path,
        path_value=PathValue(path, b), valid=True)


class _BaseListMembershipPredicate(BinaryPredicate):
  """Implements binary predicate comparison predicate for list membership."""
  # pylint: disable=abstract-method

  @property
  def strict(self):
    """Strict membership means all members must satisfy the predicate."""
    return self.__strict

  def __init__(self, name, operand, **kwargs):
    """Constructor."""
    self.__strict = kwargs.pop('strict', False)
    super(_BaseListMembershipPredicate, self).__init__(name, operand, **kwargs)

  def _verify_elem(self, context, elem, the_list):
    """Verify if |elem| is in |the_list|

    Args:
      elem [object]: The value to test.
      the_list [list]: The list of objects to test against.

    Returns:
      True if the value is a member of the list or strict checking is disabled
           and the value is a subset of a member of the list.
      False otherwise.
    """
    if self.__strict or isinstance(elem, (int, long, float, basestring)):
      return elem in context.eval(the_list)

    if self.__strict:
      return False

    pred = None
    if isinstance(elem, list):
      pred = LIST_SUBSET(elem)
    elif isinstance(elem, dict):
      # pylint: disable=redefined-variable-type
      pred = DICT_SUBSET(elem)
    else:
      raise TypeError('Unhandled type {0}'.format(elem.__class__))

    for value in the_list:
      if pred(context, value):
        return True

    return False


class ListSubsetPredicate(_BaseListMembershipPredicate):
  """Implements binary predicate comparison predicate for list subsets."""

  def __init__(self, operand, **kwargs):
    if not isinstance(operand, list):
      raise TypeError(
          '{0} is not a list: {1!r}'.format(operand.__class__, operand))
    super(ListSubsetPredicate, self).__init__('has-subset', operand, **kwargs)

  def __call__(self, context, value):
    """Determine if |operand| is a subset of |value|."""
    if not isinstance(value, list):
      return TypeMismatchError(list, value.__class__, value)

    for elem in self.eval_context_operand(context):
      if not self._verify_elem(context, elem, the_list=value):
        return PathValueResult(pred=self, valid=False,
                               path_value=PathValue('', value),
                               source=value, target_path='')

    return PathValueResult(
        pred=self, valid=True, path_value=PathValue('', value),
        source=value, target_path='')


class ListMembershipPredicate(_BaseListMembershipPredicate):
  """Implements binary predicate comparison predicate for list membership."""

  def __init__(self, operand, **kwargs):
    super(ListMembershipPredicate, self).__init__(
        'has-elem', operand, **kwargs)

  def __call__(self, context, value):
    """Determine if |operand| is a member of |value|."""
    valid = self._verify_elem(context, self.operand, the_list=value)
    return PathValueResult(
        pred=self, valid=valid, source=value, target_path='',
        path_value=PathValue('', value))


class ContainsPredicate(BinaryPredicate):
  """Specifies a predicate that expects the value "contains" the operand.

  The interpretation of "contains" depends on the value's type:
        type        | operand interpretation
        ------------+-----------------------
        basestring  | 'is-substring-of'
        dict        | 'is-subset-of'
        list        | 'is-subset-of' if operand is a list.
                    | EXISTS and element that CONTAINS operand otherwise.
        numeric     | '=='
  """

  def __init__(self, operand, **kwargs):
    super(ContainsPredicate, self).__init__('Contains', operand, **kwargs)

  def __call__(self, context, value):
    if isinstance(value, basestring):
      return STR_SUBSTR(self.operand)(context, value)
    if isinstance(value, dict):
      return DICT_SUBSET(self.operand)(context, value)
    if isinstance(value, int or long or float):
      return NUM_EQ(self.operand)(context, value)
    if not isinstance(value, list):
      raise NotImplementedError(
          'Unhandled value class {0}'.format(value.__class__))
    if isinstance(self.operand, list):
      return LIST_SUBSET(self.operand)(context, value)

    # The value is a list but operand is not a list.
    # So we'll look for existance of the operand in the list
    # by recursing on each element of the list until we find something
    # or exhaust the list.
    bad_values = []
    for elem in value:
      result = self(context, elem)
      if result:
        return result
      bad_values.append(elem)

    return PathValueResult(valid=False, pred=self,
                           source=value, target_path='',
                           path_value=PathValue('', bad_values))


class EquivalentPredicate(BinaryPredicate):
  """Specifies a predicate that expects the value and operand are "equal".

  This is similar to the type-specific '==' predicate, but is polymorphic.
  """

  def __init__(self, operand, **kwargs):
    """Constructor.

    Args:
      operand: [any] The value to compare the argument against.

      See base class (BinaryPredicate) for additional kwargs.
    """
    super(EquivalentPredicate, self).__init__('Equivalent', operand, **kwargs)

  def __check_operand_and_call(self, context, operand_type,
                               value, pred_factory):
    """Ensure the operand is of the expected type and apply the predicate.

    Args:
      operand_type: [type] The type we expect the operand to be.
      value: [any] The value we want to apply the predicate to.
      pred_factory: [method] Given the operand, constructs a binary predicate
         that performs a == comparision and returns a PredicateResult.
    Returns
      PredicateResult might be JsonTypeMismatchResult if operand_type is wrong.
    """
    operand = context.eval(self.operand)
    if not isinstance(operand, operand_type):
      return TypeMismatchError(operand_type, operand.__class__, value)
    return pred_factory(operand)(context, value)

  def __call__(self, context, value):
    """Implements the predicate by determining if value == operand."""
    if isinstance(value, basestring):
      return self.__check_operand_and_call(context, basestring, value, STR_EQ)
    if isinstance(value, dict):
      return self.__check_operand_and_call(context, dict, value, DICT_EQ)
    if isinstance(value, list):
      return self.__check_operand_and_call(context, list, value, LIST_SIMILAR)
    if isinstance(value, int or long or float):
      return self.__check_operand_and_call(context, (int, long, float),
                                           value, NUM_EQ)
    raise NotImplementedError(
        'Unhandled value class {0}'.format(value.__class__))


class DifferentPredicate(BinaryPredicate):
  """Specifies a predicate that expects the value and operand are not "equal".

  This is similar to the type-specific '!=' predicate, but is polymorphic.
  """

  def __init__(self, operand, **kwargs):
    """Constructor.

    Args:
      operand: [any] The value to compare the argument against.

      See base class (BinaryPredicate) for additional kwargs.
    """
    super(DifferentPredicate, self).__init__('Different', operand, **kwargs)

  def __check_operand_and_call(self, context, operand_type,
                               value, pred_factory):
    """Ensure the operand is of the expected type and apply the predicate.

    Args:
      operand_type: [type] The type we expect the operand to be.
      value: [any] The value we want to apply the predicate to.
      pred_factory: [method] Given the operand, constructs a binary predicate
         that performs a != comparision and returns a PredicateResult.
    Returns
      PredicateResult might be JsonTypeMismatchResult if operand_type is wrong.
    """
    operand = context.eval(self.operand)
    if not isinstance(operand, operand_type):
      return TypeMismatchError(
          operand_type, operand.__class__, value)
    return pred_factory(operand)(context, value)

  def __call__(self, context, value):
    """Implements the predicate by determining if value != operand."""
    if isinstance(value, basestring):
      return self.__check_operand_and_call(context, basestring, value, STR_NE)
    if isinstance(value, dict):
      return self.__check_operand_and_call(context, dict, value, DICT_NE)
    if isinstance(value, list):
      return self.__check_operand_and_call(context, list, value, LIST_NE)
    if isinstance(value, int or long or float):
      return self.__check_operand_and_call(
          context, (int, long, float), value, NUM_NE)
    raise NotImplementedError(
        'Unhandled value class {0}'.format(value.__class__))


NUM_LE = StandardBinaryPredicateFactory(
    '<=', lambda a, b: a <= b, operand_type=(int, long, float))
NUM_GE = StandardBinaryPredicateFactory(
    '>=', lambda a, b: a >= b, operand_type=(int, long, float))
NUM_EQ = StandardBinaryPredicateFactory(
    '==', lambda a, b: a == b, operand_type=(int, long, float))
NUM_NE = StandardBinaryPredicateFactory(
    '!=', lambda a, b: a != b, operand_type=(int, long, float))

STR_SUBSTR = StandardBinaryPredicateFactory(
    'has-substring', lambda a, b: a.find(b) >= 0, operand_type=basestring)
STR_EQ = StandardBinaryPredicateFactory(
    '==', lambda a, b: a == b, operand_type=basestring)
STR_NE = StandardBinaryPredicateFactory(
    '!=', lambda a, b: a != b, operand_type=basestring)

DICT_EQ = StandardBinaryPredicateFactory(
    '==', lambda a, b: a == b, operand_type=dict)
DICT_NE = StandardBinaryPredicateFactory(
    '!=', lambda a, b: a != b, operand_type=dict)
DICT_SUBSET = DictSubsetPredicate
DICT_MATCHES = DictMatchesPredicate

LIST_EQ = StandardBinaryPredicateFactory(
    '==', lambda a, b: a == b, operand_type=list)
LIST_NE = StandardBinaryPredicateFactory(
    '!=', lambda a, b: a != b, operand_type=list)
LIST_MATCHES = ListMatchesPredicate

def lists_equivalent(a, b):
  """Determine if two lists are equivalent without regard to order."""
  # pylint: disable=invalid-name
  if len(a) != len(b):
    return False
  sorted_a = sorted(a)
  sorted_b = sorted(b)
  for index, value in enumerate(sorted_a):
    if sorted_b[index] != value:
      return False
  return True

LIST_SIMILAR = StandardBinaryPredicateFactory(
    '~=', lambda a, b: lists_equivalent(a, b), operand_type=list)
LIST_MEMBER = (lambda operand, strict=False:
               ListMembershipPredicate(operand, strict=strict))
LIST_SUBSET = (lambda operand, strict=False:
               ListSubsetPredicate(operand, strict=strict))

CONTAINS = ContainsPredicate
EQUIVALENT = EquivalentPredicate
DIFFERENT = DifferentPredicate
