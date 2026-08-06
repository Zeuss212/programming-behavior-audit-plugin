def calculate_average(numbers):
    if not numbers:
        return 0.0

    total = 0
    for number in numbers:
        total += number

    return total / len(numbers)
print(calculate_average([10, 20, 30, 40]))
print(calculate_average([]))