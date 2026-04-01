
def test_strip_assert(string_text):
    assert len(string_text) > 0, "String should not be empty"
    return string_text.strip()


def test_strip(string_text):
    if len(string_text) > 0:
        return string_text.strip()
    else:
        raise ValueError("String should not be empty")
    
string_text = "   Hi, my name is Nick!   "

result_assert_test = test_strip_assert(string_text)
print(f"Result of test_strip_assert: '{result_assert_test}'")

result_test = test_strip(string_text)
print(f"Result of the test_strip : '{result_test}'")