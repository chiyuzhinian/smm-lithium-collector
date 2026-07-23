import pytest
from smm_collector.category_navigator import CategoryNavigator, discover_categories

DEFAULT_CATEGORIES = [{"name": "锂金属", "code": "lithium_metal"},
                      {"name": "锂矿", "code": "lithium_ore"},
                      {"name": "锂化合物", "code": "lithium_compound"}]

class FakeNavigator(CategoryNavigator):
	def __init__(self, fail=None, categories=None):
		self.categories = categories or list(DEFAULT_CATEGORIES)
		self.fail = fail
		self.seen = []

	async def switch(self, name):
		self.seen.append(name)
		if name == self.fail:
			raise RuntimeError("failed")

@pytest.mark.asyncio
async def test_three_in_order():
	n = FakeNavigator()
	ok, failed = await n.traverse(lambda c: async_value(c["name"]))
	assert n.seen == ["锂金属", "锂矿", "锂化合物"] and not failed

@pytest.mark.asyncio
async def test_failure_continues():
	n = FakeNavigator("锂矿")
	ok, failed = await n.traverse(lambda c: async_value(1))
	assert "锂矿" in failed and "锂化合物" in ok

@pytest.mark.asyncio
async def test_custom_categories():
	"""验证导航器接受任意分类列表。"""
	custom = [{"name": "钴金属"}, {"name": "镍化合物"}, {"name": "电解液"}]
	n = FakeNavigator(categories=custom)
	ok, failed = await n.traverse(lambda c: async_value(c["name"]))
	assert n.seen == ["钴金属", "镍化合物", "电解液"]
	assert not failed

async def async_value(x):
	return x
