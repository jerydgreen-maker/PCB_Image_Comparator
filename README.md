🐉 Azure Dragon Tech — PCB Geometry Comparator

    This program was created by Azure Dragon Tech (solo developer) for precise geometry copying, board contours, and component placement.

📌 About the Project

During the development of a replacement phone board, I faced the need to accurately transfer geometry from original boards. To solve this task, I developed this program for copying the geometry of the original board.

It helps me and other engineers to:

    Accurately position pads and electronic components.

    Perfectly repeat the contours of the original board.

    Compare board images, overlay them on each other, and perform calibration with high precision.

This project is open source. I will be glad if you take this project as a basis, introduce useful functions for yourself, and share them with other users!
✨ Key Features

Based on the project architecture, the program includes the following capabilities:

    Dual Model View & Alignment (Model A / Model B):

        Loading original board photos (normal and mirrored)[cite: 1].

        Overlaying images (Overlay), adjusting opacity (Opacity), and inspecting scale using the built-in magnifier (10x Original)[cite: 2].

    Powerful Importers & Exporters:

        Support for popular PCB and drawing formats: Gerber, DXF, SVG, KiCad, EasyEDA, Eagle, Excellon, CSV[cite: 3].

        Export reports to PDF, images, and data tables[cite: 3].

    Geometry & Calibration Tools:

        Precise definition of control points (ZERO, SCALE, CONTROL)[cite: 1].

        Dimension measurements, contour building, and tolerance handling (Tolerance)[cite: 1, 3].

        Auto-enhancement, sharpening, and noise reduction (Auto-enhance, Super-resolution)[cite: 3].

    User Interface (UI):

        Object Manager panel, layer tree, and change history with undo/redo support (Undo/Redo)[cite: 1, 3].

        Workflow Bar for quick calibration and alignment steps[cite: 3].

🚀 Quick Start
Requirements

    Python 3.13+[cite: 3]

    Libraries from requirements.txt[cite: 3]

Installation & Run

    Clone the repository:
    Bash

    git clone https://github.com/jerydgreen-maker/PCB_Image_Comparator.git
    cd PCB_Image_Comparator
    

    Install dependencies:
    Bash

    pip install -r requirements.txt

    Run the program:

        On Windows, you can simply run run.bat[cite: 3]

        Or via terminal:
        Bash

        python main.py

🤝 Contributing & Guidelines

I welcome community development! If you want to improve the program or add new features:

    Fork the repository.

    Create your feature branch (git checkout -b feature/CoolFeature).

    Commit your changes (git commit -m 'Add some CoolFeature').

    Push to the branch (git push origin feature/CoolFeature).

    Open a Pull Request.

The program is created by Azure Dragon Tech. I kindly ask you not to use this project for commercial purposes, and upon modification, not to change the name and logo. I will be glad if this program helps you in your projects, and you will also help other developers in theirs!
