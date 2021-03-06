# -*- coding: utf-8 -*-

# This code is part of Qiskit.
#
# (C) Copyright IBM 2018, 2020.
#
# This code is licensed under the Apache License, Version 2.0. You may
# obtain a copy of this license in the LICENSE.txt file in the root directory
# of this source tree or at http://www.apache.org/licenses/LICENSE-2.0.
#
# Any modifications or derivative works of this code must retain this
# copyright notice, and modified files need to carry a notice indicating
# that they have been altered from the originals.

""" PhaseEstimationCircuit for getting the eigenvalues of a matrix. """

from typing import Optional, List
import numpy as np
from qiskit import QuantumRegister

from qiskit.aqua.circuits import PhaseEstimationCircuit
from qiskit.aqua.operators import op_converter, BaseOperator
from qiskit.aqua.components.iqfts import IQFT
from qiskit.aqua.utils.validation import validate_min, validate_in_set
from .eigs import Eigenvalues

# pylint: disable=invalid-name


class EigsQPE(Eigenvalues):

    """ This class embeds a PhaseEstimationCircuit for getting the eigenvalues of a matrix.

    Specifically, this class is based on PhaseEstimationCircuit with no measurements and additional
    handling of negative eigenvalues, e.g. for HHL. It uses many parameters
    known from plain QPE. It depends on QFT and IQFT.
    """

    def __init__(self,
                 operator: BaseOperator,
                 iqft: IQFT,
                 num_time_slices: int = 1,
                 num_ancillae: int = 1,
                 expansion_mode: str = 'trotter',
                 expansion_order: int = 1,
                 evo_time: Optional[float] = None,
                 negative_evals: bool = False,
                 ne_qfts: Optional[List] = None) -> None:
        """Constructor.

        Args:
            operator: the hamiltonian Operator object
            iqft: the Inverse Quantum Fourier Transform component
            num_time_slices: the number of time slices, has a min. value of 1.
            num_ancillae: the number of ancillary qubits to use for the measurement,
                            has a min. value of 1.
            expansion_mode: the expansion mode (trotter|suzuki)
            expansion_order: the suzuki expansion order, has a min. value of 1.
            evo_time: the evolution time
            negative_evals: indicate if negative eigenvalues need to be handled
            ne_qfts: the QFT and IQFT components for handling negative eigenvalues
        """
        super().__init__()
        ne_qfts = ne_qfts if ne_qfts is not None else [None, None]
        validate_min('num_time_slices', num_time_slices, 1)
        validate_min('num_ancillae', num_ancillae, 1)
        validate_in_set('expansion_mode', expansion_mode, {'trotter', 'suzuki'})
        validate_min('expansion_order', expansion_order, 1)
        self._operator = op_converter.to_weighted_pauli_operator(operator)
        self._iqft = iqft
        self._num_ancillae = num_ancillae
        self._num_time_slices = num_time_slices
        self._expansion_mode = expansion_mode
        self._expansion_order = expansion_order
        self._evo_time = evo_time
        self._negative_evals = negative_evals
        self._ne_qfts = ne_qfts
        self._circuit = None
        self._output_register = None
        self._input_register = None
        self._init_constants()

    def _init_constants(self):
        # estimate evolution time
        if self._evo_time is None:
            lmax = sum([abs(p[0]) for p in self._operator.paulis])
            if not self._negative_evals:
                self._evo_time = (1-2**-self._num_ancillae)*2*np.pi/lmax
            else:
                self._evo_time = (1/2-2**-self._num_ancillae)*2*np.pi/lmax

        # check for identify paulis to get its coef for applying global
        # phase shift on ancillae later
        num_identities = 0
        for p in self._operator.paulis:
            if np.all(p[1].z == 0) and np.all(p[1].x == 0):
                num_identities += 1
                if num_identities > 1:
                    raise RuntimeError('Multiple identity pauli terms are present.')
                self._ancilla_phase_coef = p[0].real if isinstance(p[0], complex) else p[0]

    def get_register_sizes(self):
        return self._operator.num_qubits, self._num_ancillae

    def get_scaling(self):
        return self._evo_time

    def construct_circuit(self, mode, register=None):
        """ Construct the eigenvalues estimation using the PhaseEstimationCircuit

        Args:
            mode (str): construction mode, 'matrix' not supported
            register (QuantumRegister): the register to use for the quantum state

        Returns:
            QuantumCircuit: object for the constructed circuit
        Raises:
            ValueError: QPE is only possible as a circuit not as a matrix
        """

        if mode == 'matrix':
            raise ValueError('QPE is only possible as a circuit not as a matrix.')

        pe = PhaseEstimationCircuit(
            operator=self._operator, state_in=None, iqft=self._iqft,
            num_time_slices=self._num_time_slices, num_ancillae=self._num_ancillae,
            expansion_mode=self._expansion_mode, expansion_order=self._expansion_order,
            evo_time=self._evo_time
        )

        a = QuantumRegister(self._num_ancillae)
        q = register

        qc = pe.construct_circuit(state_register=q, ancillary_register=a)

        # handle negative eigenvalues
        if self._negative_evals:
            self._handle_negative_evals(qc, a)

        self._circuit = qc
        self._output_register = a
        self._input_register = q
        return self._circuit

    def _handle_negative_evals(self, qc, q):
        sgn = q[0]
        qs = [q[i] for i in range(1, len(q))]
        for qi in qs:
            qc.cx(sgn, qi)
        self._ne_qfts[0].construct_circuit(mode='circuit', qubits=qs, circuit=qc, do_swaps=False)
        for i, qi in enumerate(reversed(qs)):
            qc.cu1(2*np.pi/2**(i+1), sgn, qi)
        self._ne_qfts[1].construct_circuit(mode='circuit', qubits=qs, circuit=qc, do_swaps=False)
