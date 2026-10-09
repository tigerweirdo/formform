"""Caption'dan öğrenilen parçalar: açılış cümlesi (hook) ve hashtag'ler. Evrim motoru ve öğrenme aynı kuralı kullanır."""


def hook(caption):
    """Caption'ın ilk satırı."""
    return str('' if caption is None else caption).split('\n')[0].strip()


def tags(caption):
    """Son satırdaki hashtag'ler (#formform hariç, çünkü her videoda vardır ve bir şey öğretmez)."""
    lines = str('' if caption is None else caption).strip().split('\n')
    return [t for t in lines[-1].split() if t.startswith('#') and t != '#formform']
