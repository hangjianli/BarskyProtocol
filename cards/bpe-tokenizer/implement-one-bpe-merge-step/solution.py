def merge_once(word: tuple[str, ...], bigram: tuple[str, str]) -> tuple[str, ...]:
    first, second = bigram
    new_word = []
    i = 0

    while i < len(word):
        if i < len(word) - 1 and word[i] == first and word[i + 1] == second:
            new_word.append(first + second)
            i += 2
        else:
            new_word.append(word[i])
            i += 1

    return tuple(new_word)