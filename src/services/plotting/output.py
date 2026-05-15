import os

def createFolderForPlots(name):
    path = os.path.join('outputs/plots', name)
    os.makedirs(path, exist_ok=True)  # Creează toate directoarele din cale dacă nu există deja
    print(f"Folder '{path}' created successfully")

def checkForFolder(name):
    path = os.path.join("..", "..", "outputs", "plots", name)
    if os.path.isdir(path):
        print("Folder already exists")
    else:
        createFolderForPlots(name)


def checkForFile(original_path, name):
    path = os.path.join(original_path, name)
    if os.path.isfile(path):
        print("File already exists")
        return path
    else:
        path = "NULL"
        return path